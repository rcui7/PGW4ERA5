#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
description     Auxiliary functions for PGW for ERA5
authors		    Before 2022: original developments by Roman Brogli
                Since 2022:  upgrade to PGW for ERA5 by Christoph Heim 
"""
##############################################################################
import os, math
import xarray as xr
import numpy as np
from numba import njit
from datetime import datetime,timedelta
from constants import CON_RD, CON_G
from settings import *

## TODO DEBUG
#from matplotlib import pyplot as plt
##############################################################################


##############################################################################
##### ARBITRARY FUNCTIONS
##############################################################################
def dt64_to_dt(date):
    """
    Converts a numpy datetime64 object to a python datetime object 
    Input:
      date - a np.datetime64 object
    Output:
      DATE - a python datetime object
    source: 
      https://gist.github.com/blaylockbk/1677b446bc741ee2db3e943ab7e4cabd
    """
    timestamp = ((date - np.datetime64('1970-01-01T00:00:00'))
                 / np.timedelta64(1, 's'))
    return datetime.utcfromtimestamp(timestamp)


##############################################################################
##### PHYSICAL COMPUTATIONS
##############################################################################
def specific_to_relative_humidity(hus, pa, ta):
    """
    Compute relative humidity from specific humidity.
    """
    hur = 0.263 * pa * hus *(np.exp(17.67*(ta - 273.15)/(ta-29.65)))**(-1)
    return(hur)


def relative_to_specific_humidity(hur, pa, ta):
    """
    Compute specific humidity from relative humidity.
    """
    hus = (hur  * np.exp(17.67 * (ta - 273.15)/(ta - 29.65))) / (0.263 * pa)
    return(hus)


def integ_geopot(pa_hl, zgs, ta, hus, level1, p_ref):
    """
    Integrate ERA5 geopotential from surfce to a reference pressure
    level p_ref.
    """
    ## take log half-level pressure difference (located at full levels)
    # make sure pressure is not exactly zero because of ln
    pa_hl = pa_hl.where(pa_hl > 0, 0.0001)
    dlnpa = np.log(pa_hl).diff(
                dim=VERT_HL_ERA, 
                label='lower').rename({VERT_HL_ERA:VERT_ERA})

    # create geopotential array and fill with surface geopotential
    phi_hl = zgs.expand_dims(dim={VERT_HL_ERA:level1}).copy()

    # compute virtual temperature
    tav = ta * (1 + 0.61 * hus)

    ## integrate over model half levels
    for l in sorted(tav[VERT_ERA].values, reverse=True):
        # geopotential at full level
        phi_hl.loc[{VERT_HL_ERA:l}] = (
                phi_hl.sel({VERT_HL_ERA:l+1}) +
                (CON_RD * tav.sel({VERT_ERA:l}) * dlnpa.sel({VERT_ERA:l}))
        )

            
    phi_hl = phi_hl.transpose(TIME_ERA, VERT_HL_ERA, LAT_ERA, LON_ERA)

    ## integrate from last half level below reference pressure
    ## up to reference pressure
    # determine level below reference pressure
    p_diff = pa_hl - p_ref
    p_diff = p_diff.where(p_diff >= 0, np.nan)
    ind_ref_star = p_diff.argmin(dim=VERT_HL_ERA)
    hl_ref_star = p_diff[VERT_HL_ERA].isel({VERT_HL_ERA:ind_ref_star})
    # get pressure and geopotential of that level
    p_ref_star = pa_hl.sel({VERT_HL_ERA:hl_ref_star})
    phi_ref_star = phi_hl.sel({VERT_HL_ERA:hl_ref_star})

    # finally interpolate geopotential to reference
    # pressure level
    phi_ref = (
            phi_ref_star -
            (CON_RD * tav.sel({VERT_ERA:hl_ref_star-1})) * 
            (np.log(p_ref) - np.log(p_ref_star))
    )

    # remove multi-dimensional coordinates
    if VERT_HL_ERA in phi_ref.coords:
        del phi_ref[VERT_HL_ERA]
    if VERT_ERA in phi_ref.coords:
        del phi_ref[VERT_ERA]
    if PLEV_GCM in phi_ref.coords:
        del phi_ref[VERT_ERA]

    return(phi_ref)


##############################################################################
##### CLIMATE DELTA COMPUTATION AND INTERPOLATION
##############################################################################
def load_delta(delta_inp_path, var_name, era_date_time, 
               delta_date_time=None,
               name_base=climate_delta_file_name_base):
    """
    Load a climate delta and if delta_date_time is given,
    interpolate it to that date and time of the year.
    """
    ## full climate delta (either daily or monthly)
    full_delta = xr.open_dataset(os.path.join(delta_inp_path,
                            name_base.format(var_name)))

    ## if climate delta should be interpolated to a specific time
    if delta_date_time is not None:
        # replace delta year values with year of current delta_date_time
        for i in range(len(full_delta.time)):
            full_delta.time.values[i] = dt64_to_dt(
                        full_delta.time[i]).replace(
                                year=delta_date_time.year)

        # add periodicity at the start and the end of the
        # annual cycle
        last_year = full_delta.isel(time=-1)
        last_year.time.values = dt64_to_dt(
                    last_year.time).replace(
                            year=delta_date_time.year-1)
        next_year = full_delta.isel(time=0)
        next_year.time.values = dt64_to_dt(
                    next_year.time).replace(
                            year=delta_date_time.year+1)
        full_delta = xr.concat([last_year, full_delta, next_year],
                                dim='time')

        # interpolate in time and select variable
        delta = full_delta[var_name].interp(time=delta_date_time, 
                                    method='linear', 
                                ).expand_dims(dim='time', axis=0)

        # make sure time is in the same format as in ERA5 file
        # ERA5 has "seconds since xyz" while delta has np.datetime64
        delta['time'] = era_date_time

    ## if full climate delta should be returned without 
    ## time interpolation
    else:
        delta = full_delta[var_name]

    return(delta)


## TODO DEBUG START
def load_delta_old(delta_inp_path, var_name, era_date_time, 
               delta_date_time=None, name_base='{}_delta.nc'):
    """
    Load a climate delta and if delta_date_time is given,
    interpolate it to that date and time of the year.
    """

    def hour_of_year(dt): 
        beginning_of_year = datetime(dt.year, 1, 1, tzinfo=dt.tzinfo)
        return(int((dt - beginning_of_year).total_seconds() // 3600))
    name_base = name_base.split('.nc')[0] + '_{:05d}.nc'
    diff_time_step = int(hour_of_year(delta_date_time)/3)
    delta = xr.open_dataset(os.path.join(delta_inp_path,
            name_base.format(var_name, diff_time_step)))[var_name]
    # make sure time is in the same format as in laf file
    delta['time'] = era_date_time

    return(delta)
## TODO DEBUG STOP

def load_delta_interp(delta_inp_path, var_name, target_P,
                    era_date_time, delta_date_time,
                    ignore_top_pressure_error=False):
    """
    Does the following:
        - load a climate delta
        - for specific variables (ta and hur) also load surface value
          as well as historical surface pressure. This is to extend
          the 3D climate deltas with surface values which makes
          the interpolation to the ERA5 model levels more precise.
        - vertically interpolate climate deltas to ERA5 model levels
    """
    delta = load_delta(delta_inp_path, var_name, 
                        era_date_time, delta_date_time)

    ## for specific variables also load climate delta for surface
    ## values and the historical surface pressure.
    if var_name in ['ta','hur']:
        sfc_var_name = var_name + 's'
        delta_sfc = load_delta(delta_inp_path, sfc_var_name, 
                            era_date_time, delta_date_time)
        ps_hist = load_delta(delta_inp_path, 'ps', 
                            era_date_time, delta_date_time,
                            name_base=era_climate_file_name_base)
    else:
        delta_sfc = None
        ps_hist = None

    # interpolate climate delta onto ERA5 model levels
    delta = vert_interp_delta(delta, target_P, delta_sfc, ps_hist,
                            ignore_top_pressure_error)
    return(delta)


def replace_delta_sfc(source_P, ps_hist, delta, delta_sfc):
    """
    In the 3D climate deltas, replace the value just below
    the surface by the surface climate delta value and insert
    it a historical surface pressure. This improves the precision
    of the climate deltas during interpolation to the ERA5 model levels.
    All 3D climate delta values below the historical surface pressure
    are set to the surface value (constant extrapolation). This is
    because within the orography the GCM climate delta is assumed
    to be incorrect.
    """
    out_source_P = source_P.copy()
    out_delta = delta.copy()
    if ps_hist > np.max(source_P):
        sfc_ind = len(source_P) - 1
        out_source_P[sfc_ind] = ps_hist
        out_delta[sfc_ind] = delta_sfc
    elif ps_hist < np.min(source_P):
        raise ValueError()
    else:
        sfc_ind = np.max(np.argwhere(ps_hist > source_P))
        out_delta[sfc_ind:] = delta_sfc
        out_source_P[sfc_ind] = ps_hist
    return(out_source_P, out_delta)


def vert_interp_delta(delta, target_P, delta_sfc=None, ps_hist=None,
                       ignore_top_pressure_error=False):
    """
    Vertically interpolate climate delta onto ERA5 model levels.
    If delta_sfc and ps_hist are given, surface values will
    be inserted into the 3D climate delta at the height of
    the surface pressure. This gives a more precise interpolation.
    Climate delta values below the surface are set to the surface
    climate delta because below the surface, the GCM climate delta
    is considered unreliable and thus constant extrapolation
    seems more reasonable.
    """

    # sort delta dataset from top to bottom (pressure ascending)
    delta = delta.reindex(
                {PLEV_GCM:list(reversed(delta[PLEV_GCM]))})

    # create 4D source pressure with GCM pressure levels
    source_P = delta[PLEV_GCM].expand_dims(
                    dim={LON_GCM:delta[LON_GCM],
                         LAT_GCM:delta[LAT_GCM],
                         TIME_GCM:delta[TIME_GCM]}).transpose(
                                TIME_GCM, PLEV_GCM, LAT_GCM, LON_GCM)

    ## if surface values are given, replace them at the
    ## level of the surface pressure
    if delta_sfc is not None:
        source_P, delta = xr.apply_ufunc(
                replace_delta_sfc, source_P, 
                ps_hist, 
                delta, delta_sfc,
                input_core_dims=[[PLEV_GCM],[],[PLEV_GCM],[]],
                output_core_dims=[[PLEV_GCM],[PLEV_GCM]],
                vectorize=True)
        source_P = source_P.transpose(TIME_GCM, PLEV_GCM, LAT_GCM, LON_GCM)
        delta = delta.transpose(TIME_GCM, PLEV_GCM, LAT_GCM, LON_GCM)

    # make sure all arrays contain the required dimensions
    if source_P.dims != (TIME_GCM, PLEV_GCM, LAT_GCM, LON_GCM):
        raise ValueError()
    if delta.dims != (TIME_GCM, PLEV_GCM, LAT_GCM, LON_GCM):
        raise ValueError()
    if target_P.dims != (TIME_ERA, VERT_ERA, LAT_ERA, LON_ERA):
        raise ValueError()

    # make sure there is no extrapolation at the model top
    # unless these levels are anyways not important for the user
    # and she/he manually sets ignore_top_pressure_error=True
    if np.min(target_P) < np.min(source_P):
        if not ignore_top_pressure_error:
            raise ValueError('ERA5 top pressure is lower than '+
                             'climate delta top pressure. If you are ' +
                             'certain that you do not need the data ' +
                             'beyond to upper-most pressure level of the ' +
                             'climate delta, you can set the flag ' +
                             '--ignore_top_pressure_error and re-run the ' +
                             'script.')
                             

    # run interpolation
    delta_interp = interp_logp_3d(delta, source_P, target_P,
                        extrapolate='constant')
    return(delta_interp)


def interp_logp_3d(var, source_P, targ_P, extrapolate='off'):
    """
    Interpolate 3D array in vertical (pressure) dimension using the
    logarithm of pressure.
    extrapolate:
        - off: no extrapolation
        - linear: linear extrapolation
        - constant: constant extrapolation
    """
    if extrapolate not in ['off', 'linear', 'constant']:
        raise ValueError()

    targ = xr.zeros_like(targ_P)
    tmp = np.zeros_like(targ.values.squeeze())
    interp_1d_for_latlon(var.values.squeeze(),
                np.log(source_P.values.squeeze()),
                np.log(targ_P.squeeze()).values, 
                tmp,
                len(targ_P[LAT_ERA]), len(targ_P[LON_ERA]),
                extrapolate)
    tmp = np.expand_dims(tmp, axis=0)
    targ.values = tmp
    return(targ)



@njit()
def interp_1d_for_latlon(orig_array, src_p, targ_p, interp_array,
                        nlat, nlon, extrapolate):
    """
    Vertical interpolation helper function with numba njit for 
    fast performance.
    Loop over lat and lon dimensions and interpolate each column
    individually
    extrapolate:
        - off: no extrapolation
        - linear: linear extrapolation
        - constant: constant extrapolation
    """
    for lat_ind in range(nlat):
        for lon_ind in range(nlon):
            src_val_col = orig_array[:, lat_ind, lon_ind]
            src_p_col = src_p[:, lat_ind, lon_ind]
            targ_p_col = targ_p[:, lat_ind, lon_ind]

            # call 1D interpolation function for current column
            interp_col = interp_extrap_1d(src_p_col, src_val_col, 
                                        targ_p_col, extrapolate)
            interp_array[:, lat_ind, lon_ind] = interp_col


@njit()
def interp_extrap_1d(src_x, src_y, targ_x, extrapolate):
    """
    Numba helper function for interpolation of 1d vertical column.
    Does constant extrapolation which is used for the climate deltas.
    extrapolate:
        - off: no extrapolation
        - linear: linear extrapolation
        - constant: constant extrapolation
    """
    targ_y = np.zeros(len(targ_x))
    for ti in range(len(targ_x)):
        i1 = -1
        i2 = -1
        require_extrap = False
        for si in range(len(src_x)):
            ty = np.nan
            # extrapolate lower end
            if (si == 0) and src_x[si] > targ_x[ti]:
                if extrapolate == 'linear':
                    i1 = si
                    i2 = si + 1
                elif extrapolate == 'constant':
                    i1 = si
                    i2 = si
                require_extrap = True
                break
            # exact match
            elif src_x[si] == targ_x[ti]:
                i1 = si
                i2 = si
                break
            # upper src_x found (interpolation)
            elif src_x[si] > targ_x[ti]:
                i1 = si - 1
                i2 = si
                break
            # we are still smaller than targ_x[ti] 
            else:
                pass

        # extrapolate upper end
        if i1 == -1:
            if extrapolate == 'linear':
                i1 = len(src_x) - 2
                i2 = len(src_x) - 1
            elif extrapolate == 'constant':
                i1 = len(src_x) - 1 
                i2 = len(src_x) - 1
            require_extrap = True

        # raise value if extrapolation is required but not enabled.
        if require_extrap and extrapolate == 'off':
            raise ValueError('Extrapolation deactivated but data '+
                             'out of bounds.')

        # interpolate/extrapolate values
        if i1 == i2:
            targ_y[ti] = src_y[i1]
        else:
            targ_y[ti] = (
                src_y[i1] + (targ_x[ti] - src_x[i1]) * 
                (src_y[i2] - src_y[i1]) / (src_x[i2] - src_x[i1])
            )

    return(targ_y)


def determine_p_ref(ps_era, ps_pgw, p_ref_opts, p_ref_last=None):
    """
    Find lowest GCM pressure level among p_ref_opts that lies above 
    surface (surface pressure) in both ERA and PGW climate.
    Also ensure that during the iterations, no reference pressure level 
    at lower altitude than during last iterations is used. This is to
    prevent the iteration algorithm to oscillate between two reference
    pressure levels and not converge.
    """
    for p in p_ref_opts:
        if (ps_era > p) & (ps_pgw > p):
            if p_ref_last is None:
                return(p)
            else:
                return(min(p, p_ref_last))




##############################################################################
##### SMOOTHING OF ANNUAL CYCLE FOR DAILY CLIMATE DELTAS
##############################################################################
def filter_data(annualcycleraw, variablename_to_smooth, outputpath):

	"""
	This function performs a temporal smoothing of an annual timeseries 
    (typically daily resolution) using a spectral filter 
    (Bosshard et al. 2011).

	Input:
		Input 1: Path to a netcdf file of the annual cycle to be smoothed. 
        Normally this is the change in a specific variable between 
        two simulations (e.g. warming). 
        Can be 4 or 3 dimensional, where the time is one dimension 
        and the others are space dimensions.
		Input 2: The name of the variable within the given netcdf file
		Input 3: Path to the output file
		
	Output:
		A netcdf file containing the smoothed annual cycle. 
	"""	

	Diff = xr.open_dataset(annualcycleraw
                )[variablename_to_smooth].squeeze()
	coords = Diff.coords

	print('Dimension that is assumed to be time dimension is called: ', 
            Diff.dims[0])
	print('shape of data: ', Diff.shape)

	Diff = Diff.data

	#create an array to store the smoothed timeseries
	#Diff_smooth=np.zeros_like(Diff, dtype=np.float32) 

	if len(Diff.shape) == 4:
		times = Diff.shape[0] 
		levels = Diff.shape[1]
		ygrids = Diff.shape[2]
		xgrids = Diff.shape[3]
	elif len(Diff.shape) == 3:
		times = Diff.shape[0]
		ygrids = Diff.shape[1]
		xgrids = Diff.shape[2]
		levels = 0
	else:
		sys.exit('Wrog dimensions of input file should be 3 or 4-D')


	if len(Diff.shape) == 4:
        #loop over levels to smooth the timeseries on every level
		for i in range(levels):
			for yy in range(ygrids):
				for xx in range(xgrids):	
                    #reconstruct the smoothed timeseries using function below
					Diff[:,i,yy,xx] = harmonic_ac_analysis(Diff[:,i,yy,xx]) 



	if len(Diff.shape) == 3:		
		for yy in range(ygrids):
			for xx in range(xgrids):	
            #dump the smoothed timeseries in the array on the original level
				Diff[:,yy,xx] = harmonic_ac_analysis(Diff[:,yy,xx]) 
			

	print('Done with smoothing')

	#del Diff

	Diff = xr.DataArray(Diff, coords=coords, name=variablename_to_smooth)
	Diff.to_netcdf(outputpath, mode='w')


def harmonic_ac_analysis(ts):
	"""
	Estimation of the harmonics according to formula 12.19 -
	12.23 on p. 264 in Storch & Zwiers

	Is incomplete since it is only for use in surrogate smoothing 
    --> only the part of the formulas that is needed there

	Arguments:
		ts: a 1-d numpy array of a timeseries

	Returns:
		hcts: a reconstructed smoothed timeseries 
                (the more modes are summed the less smoothing)
		mean: the mean of the timeseries (needed for reconstruction)
	"""
	
	if np.any(np.isnan(ts) == True): #if there are nans, return nans
		smooths = np.full_like(ts, np.nan) #sys.exit('There are nan values')
		return smooths
	else:
        #calculate the mean of the timeseries (used for reconstruction)
		mean = ts.mean() 
	
		lt = len(ts) #how long is the timeseries?
		P = lt

		#initialize the output array. 
        #we will use at max 4 modes for reconstruction 
        #(for peformance reasons, it can be increased)
		hcts = np.zeros((4,lt))

		timevector=np.arange(1,lt+1,1)	#timesteps used in calculation	

        #a measure that is to check that the performed calculation 
        # is justified.
		q = math.floor(P/2.) 
	
        #create the reconstruction timeseries, mode by mode 
        #(starting at 1 until 5, if one wants more smoothing 
        #this number can be increased.)
		for i in range(1,4): 
			if i < q: #only if this is true the calculation is valid
			
				#these are the formulas from Storch & Zwiers
				bracket = 2.*math.pi*i/P*timevector
				a = 2./lt*(ts.dot(np.cos(bracket))) 
                #dot product (Skalarprodukt) for scalar number output!
				b = 2./lt*(ts.dot(np.sin(bracket))) 
				
                #calculate the reconstruction time series
				hcts[i-1,:] = a * np.cos(bracket) + b * np.sin(bracket) 
			
			else: #abort if the above condition is not fulfilled. In this case more programming is needed.
				sys.exit('Whooops that should not be the case for a yearly '+
                'timeseries! i (reconstruction grade) is larger than '+
                'the number of timeseries elements / 2.')

		smooths = sum(hcts[0:3,:]) + mean
		return smooths
