#!/usr/bin/env python3

import os
import geopandas as gpd
import pandas as pd
from numpy import unique
from rasterstats import zonal_stats
import json
import argparse
import sys
from utils.shared_functions import getDriver
# sys.path.append('/foss_fim/src')
# sys.path.append('/foss_fim/config')
from utils.shared_functions import getDriver, mem_profile
from utils.shared_variables import FIM_ID
from memory_profiler import profile

# Feb 17, 2023
# We want to explore using FR methodology as branch zero

@mem_profile
def add_crosswalk(input_catchments_fileName,
                  input_flows_fileName,
                  input_srcbase_fileName,
                  output_catchments_fileName,
                  output_flows_fileName,
                  output_src_fileName,
                  output_src_json_fileName,
                  output_crosswalk_fileName,
                  output_hydro_table_fileName,
                  input_huc_fileName,
                  input_nwmflows_fileName,
                  input_nwmcatras_fileName,
                  mannings_n,
                  input_nwmcat_fileName,
                  extent,
                  small_segments_filename,
                  min_catchment_area,
                  min_stream_length,
                  calibration_mode=False):

    input_catchments = gpd.read_file(input_catchments_fileName)
    input_flows = gpd.read_file(input_flows_fileName)
    input_huc = gpd.read_file(input_huc_fileName)
    input_nwmflows = gpd.read_file(input_nwmflows_fileName)
    min_catchment_area = float(min_catchment_area) #0.25#
    min_stream_length = float(min_stream_length) #0.5#

    if extent == 'FR':
        ## crosswalk using majority catchment method

        # calculate majority catchments
        majority_calc = zonal_stats(input_catchments, input_nwmcatras_fileName, stats=['majority'], geojson_out=True)
        input_majorities = gpd.GeoDataFrame.from_features(majority_calc)
        input_majorities = input_majorities.rename(columns={'majority' : 'feature_id'})

        input_majorities = input_majorities[:][input_majorities['feature_id'].notna()]
        if input_majorities.feature_id.dtype != 'int': input_majorities.feature_id = input_majorities.feature_id.astype(int)
        if input_majorities.HydroID.dtype != 'int': input_majorities.HydroID = input_majorities.HydroID.astype(int)

        input_nwmflows = input_nwmflows.rename(columns={'ID':'feature_id'})
        if input_nwmflows.feature_id.dtype != 'int': input_nwmflows.feature_id = input_nwmflows.feature_id.astype(int)
        relevant_input_nwmflows = input_nwmflows[input_nwmflows['feature_id'].isin(input_majorities['feature_id'])]
        relevant_input_nwmflows = relevant_input_nwmflows.filter(items=['feature_id','order_'])

        if input_catchments.HydroID.dtype != 'int': input_catchments.HydroID = input_catchments.HydroID.astype(int)
        output_catchments = input_catchments.merge(input_majorities[['HydroID','feature_id']],on='HydroID')
        output_catchments = output_catchments.merge(relevant_input_nwmflows[['order_','feature_id']],on='feature_id')

        if input_flows.HydroID.dtype != 'int': input_flows.HydroID = input_flows.HydroID.astype(int)
        output_flows = input_flows.merge(input_majorities[['HydroID','feature_id']],on='HydroID')
        if output_flows.HydroID.dtype != 'int': output_flows.HydroID = output_flows.HydroID.astype(int)
        output_flows = output_flows.merge(relevant_input_nwmflows[['order_','feature_id']],on='feature_id')
        output_flows = output_flows.merge(output_catchments.filter(items=['HydroID','areasqkm']),on='HydroID')

    elif (extent == 'MS') | (extent == 'GMS'):
        ## crosswalk using stream segment midpoint method
        input_nwmcat = gpd.read_file(input_nwmcat_fileName, mask=input_huc)

        # only reduce nwm catchments to mainstems if running mainstems
        if extent == 'MS':
            input_nwmcat = input_nwmcat.loc[input_nwmcat.mainstem==1]

        input_nwmcat = input_nwmcat.rename(columns={'ID':'feature_id'})
        if input_nwmcat.feature_id.dtype != 'int': input_nwmcat.feature_id = input_nwmcat.feature_id.astype(int)
        input_nwmcat=input_nwmcat.set_index('feature_id')

        input_nwmflows = input_nwmflows.rename(columns={'ID':'feature_id'})
        if input_nwmflows.feature_id.dtype != 'int': input_nwmflows.feature_id = input_nwmflows.feature_id.astype(int)

        # Get stream midpoint
        stream_midpoint = []
        hydroID = []
        for i,lineString in enumerate(input_flows.geometry):
            hydroID = hydroID + [input_flows.loc[i,'HydroID']]
            stream_midpoint = stream_midpoint + [lineString.interpolate(0.5,normalized=True)]

        input_flows_midpoint = gpd.GeoDataFrame({'HydroID':hydroID, 'geometry':stream_midpoint}, crs=input_flows.crs, geometry='geometry')
        input_flows_midpoint = input_flows_midpoint.set_index('HydroID')

        # Create crosswalk
        crosswalk = gpd.sjoin(input_flows_midpoint, input_nwmcat, how='left', predicate='within').reset_index()
        crosswalk = crosswalk.rename(columns={"index_right": "feature_id"})

        # fill in missing ms
        crosswalk_missing = crosswalk.loc[crosswalk.feature_id.isna()]
        for index, stream in crosswalk_missing.iterrows():

            # find closest nwm catchment by distance
            distances = [stream.geometry.distance(poly) for poly in input_nwmcat.geometry]
            min_dist = min(distances)
            nwmcat_index=distances.index(min_dist)

            # update crosswalk
            crosswalk.loc[crosswalk.HydroID==stream.HydroID,'feature_id'] = input_nwmcat.iloc[nwmcat_index].name
            crosswalk.loc[crosswalk.HydroID==stream.HydroID,'AreaSqKM'] = input_nwmcat.iloc[nwmcat_index].AreaSqKM
            crosswalk.loc[crosswalk.HydroID==stream.HydroID,'Shape_Length'] = input_nwmcat.iloc[nwmcat_index].Shape_Length
            crosswalk.loc[crosswalk.HydroID==stream.HydroID,'Shape_Area'] = input_nwmcat.iloc[nwmcat_index].Shape_Area

        crosswalk = crosswalk.filter(items=['HydroID', 'feature_id'])
        crosswalk = crosswalk.merge(input_nwmflows[['feature_id','order_']],on='feature_id')

        if len(crosswalk) < 1:
            print ("No relevant streams within HUC boundaries.")
            sys.exit(0)

        if input_catchments.HydroID.dtype != 'int': input_catchments.HydroID = input_catchments.HydroID.astype(int)
        output_catchments = input_catchments.merge(crosswalk,on='HydroID')

        if input_flows.HydroID.dtype != 'int': input_flows.HydroID = input_flows.HydroID.astype(int)
        output_flows = input_flows.merge(crosswalk,on='HydroID')

        # added for GMS. Consider adding filter_catchments_and_add_attributes.py to run_by_branch.sh
        if 'areasqkm' not in output_catchments.columns:
            output_catchments['areasqkm'] = output_catchments.geometry.area/(1000**2)

        output_flows = output_flows.merge(output_catchments.filter(items=['HydroID','areasqkm']),on='HydroID')

    output_flows['ManningN'] = mannings_n

    if output_flows.NextDownID.dtype != 'int': output_flows.NextDownID = output_flows.NextDownID.astype(int)

    # Adjust short model reach rating curves
    print("Adjusting model reach rating curves")
    sml_segs = pd.DataFrame()

    # replace small segment geometry with neighboring stream
    for stream_index in output_flows.index:
        if output_flows["areasqkm"][stream_index] < min_catchment_area and output_flows["LengthKm"][stream_index] < min_stream_length and output_flows["LakeID"][stream_index] < 0:

            short_id = output_flows['HydroID'][stream_index]
            to_node = output_flows['To_Node'][stream_index]
            from_node = output_flows['From_Node'][stream_index]

            # multiple upstream segments
            if len(output_flows.loc[output_flows['NextDownID'] == short_id]['HydroID']) > 1:
                try:
                    max_order = max(output_flows.loc[output_flows['NextDownID'] == short_id]['order_']) # drainage area would be better than stream order but we would need to calculate
                except:
                    print(f"short_id {short_id} cannot calculate max stream order for multiple upstream segments scenario")

                if len(output_flows.loc[(output_flows['NextDownID'] == short_id) & (output_flows['order_'] == max_order)]['HydroID']) == 1:
                    update_id = output_flows.loc[(output_flows['NextDownID'] == short_id) & (output_flows['order_'] == max_order)]['HydroID'].item()

                else:
                    update_id = output_flows.loc[(output_flows['NextDownID'] == short_id) & (output_flows['order_'] == max_order)]['HydroID'].values[0] # get the first one (same stream order, without drainage area info it is hard to know which is the main channel)

            # single upstream segments
            elif len(output_flows.loc[output_flows['NextDownID'] == short_id]['HydroID']) == 1:
                update_id = output_flows.loc[output_flows.To_Node==from_node]['HydroID'].item()

            # no upstream segments; multiple downstream segments
            elif len(output_flows.loc[output_flows.From_Node==to_node]['HydroID']) > 1:
                try:
                    max_order = max(output_flows.loc[output_flows.From_Node==to_node]['HydroID']['order_']) # drainage area would be better than stream order but we would need to calculate
                except:
                    print(f"To Node {to_node} cannot calculate max stream order for no upstream segments; multiple downstream segments scenario")

                if len(output_flows.loc[(output_flows['NextDownID'] == short_id) & (output_flows['order_'] == max_order)]['HydroID']) == 1:
                    update_id = output_flows.loc[(output_flows.From_Node==to_node) & (output_flows['order_'] == max_order)]['HydroID'].item()

                else:
                    update_id = output_flows.loc[(output_flows.From_Node==to_node) & (output_flows['order_'] == max_order)]['HydroID'].values[0] # get the first one (same stream order, without drainage area info it is hard to know which is the main channel)

            # no upstream segments; single downstream segment
            elif len(output_flows.loc[output_flows.From_Node==to_node]['HydroID']) == 1:
                    update_id = output_flows.loc[output_flows.From_Node==to_node]['HydroID'].item()

            else:
                update_id = output_flows.loc[output_flows.HydroID==short_id]['HydroID'].item()

            str_order = output_flows.loc[output_flows.HydroID==short_id]['order_'].item()
            sml_segs = pd.concat([sml_segs, pd.DataFrame({'short_id':[short_id], 'update_id':[update_id], 'str_order':[str_order]})], ignore_index=True)

    print("Number of short reaches [{} < {} and {} < {}] = {}".format("areasqkm", min_catchment_area, "LengthKm", min_stream_length, len(sml_segs)))

    # calculate src_full
    input_src_base = pd.read_csv(input_srcbase_fileName, dtype= object)
    if input_src_base.CatchId.dtype != 'int': input_src_base.CatchId = input_src_base.CatchId.astype(int)

    input_src_base = input_src_base.merge(output_flows[['ManningN','HydroID','NextDownID','order_']],left_on='CatchId',right_on='HydroID')

    input_src_base = input_src_base.rename(columns=lambda x: x.strip(" "))
    input_src_base = input_src_base.apply(pd.to_numeric,**{'errors' : 'coerce'})
    input_src_base['TopWidth (m)'] = input_src_base['SurfaceArea (m2)']/input_src_base['LENGTHKM']/1000
    input_src_base['WettedPerimeter (m)'] = input_src_base['BedArea (m2)']/input_src_base['LENGTHKM']/1000
    input_src_base['WetArea (m2)'] = input_src_base['Volume (m3)']/input_src_base['LENGTHKM']/1000
    input_src_base['HydraulicRadius (m)'] = input_src_base['WetArea (m2)']/input_src_base['WettedPerimeter (m)']
    input_src_base['HydraulicRadius (m)'].fillna(0, inplace=True)
    input_src_base['Discharge (m3s-1)'] = input_src_base['WetArea (m2)']* \
    pow(input_src_base['HydraulicRadius (m)'],2.0/3)* \
    pow(input_src_base['SLOPE'],0.5)/input_src_base['ManningN']

    # set nans to 0
    input_src_base.loc[input_src_base['Stage']==0,['Discharge (m3s-1)']] = 0

    output_src = input_src_base.drop(columns=['CatchId'])
    if output_src.HydroID.dtype != 'int': output_src.HydroID = output_src.HydroID.astype(int)

    # update rating curves
    if len(sml_segs) > 0:

        sml_segs.to_csv(small_segments_filename,index=False)
        print("Update rating curves for short reaches.")

        for index, segment in sml_segs.iterrows():

            short_id = segment[0]
            update_id= segment[1]
            new_values = output_src.loc[output_src['HydroID'] == update_id][['Stage', 'Discharge (m3s-1)']]

            for src_index, src_stage in new_values.iterrows():
                output_src.loc[(output_src['HydroID']== short_id) & (output_src['Stage']== src_stage[0]),['Discharge (m3s-1)']] = src_stage[1]

    if extent == 'FR':
        output_src = output_src.merge(input_majorities[['HydroID','feature_id']],on='HydroID')
    elif (extent == 'MS') | (extent == 'GMS'):
        output_src = output_src.merge(crosswalk[['HydroID','feature_id']],on='HydroID')

    output_crosswalk = output_src[['HydroID','feature_id']]
    output_crosswalk = output_crosswalk.drop_duplicates(ignore_index=True)

    ## bathy estimation integration in synthetic rating curve calculations
    #if (bathy_src_calc == True and extent == 'MS'):
    #    output_src = bathy_rc_lookup(output_src,input_bathy_fileName,output_bathy_fileName,output_bathy_streamorder_fileName,output_bathy_thalweg_fileName,output_bathy_xs_lookup_fileName)
    #else:
    #    print('Note: NOT using bathy estimation approach to modify the SRC...')

    # make hydroTable
    output_hydro_table = output_src.loc[:,['HydroID','feature_id','NextDownID','order_','Number of Cells','SurfaceArea (m2)','BedArea (m2)','TopWidth (m)','LENGTHKM','AREASQKM','WettedPerimeter (m)','HydraulicRadius (m)','WetArea (m2)','Volume (m3)','SLOPE','ManningN','Stage','Discharge (m3s-1)']]
    output_hydro_table.rename(columns={'Stage' : 'stage','Discharge (m3s-1)':'discharge_cms'},inplace=True)
    ## Set placeholder variables to be replaced in post-processing (as needed). Create here to ensure consistent column vars
    ## These variables represent the original unmodified values
    output_hydro_table['default_discharge_cms'] = output_src['Discharge (m3s-1)']
    output_hydro_table['default_Volume (m3)'] = output_src['Volume (m3)']
    output_hydro_table['default_WetArea (m2)'] = output_src['WetArea (m2)']
    output_hydro_table['default_HydraulicRadius (m)'] = output_src['HydraulicRadius (m)']
    output_hydro_table['default_ManningN'] = output_src['ManningN']
    ## Placeholder vars for subdivision routine
    output_hydro_table['subdiv_applied'] = False
    output_hydro_table['overbank_n'] = pd.NA
    output_hydro_table['channel_n'] = pd.NA
    output_hydro_table['subdiv_discharge_cms'] = pd.NA
    ## Placeholder vars for the calibration routine
    output_hydro_table['calb_applied'] = False
    output_hydro_table['last_updated'] = pd.NA
    output_hydro_table['submitter'] = pd.NA
    output_hydro_table['obs_source'] = pd.NA
    output_hydro_table['precalb_discharge_cms'] = pd.NA
    output_hydro_table['calb_coef_usgs'] = pd.NA
    output_hydro_table['calb_coef_spatial'] = pd.NA
    output_hydro_table['calb_coef_final'] = pd.NA

    if output_hydro_table.HydroID.dtype != 'str': output_hydro_table.HydroID = output_hydro_table.HydroID.astype(str)
    output_hydro_table[FIM_ID] = output_hydro_table.loc[:,'HydroID'].apply(lambda x : str(x)[0:4])

    if input_huc[FIM_ID].dtype != 'str': input_huc[FIM_ID] = input_huc[FIM_ID].astype(str)
    output_hydro_table = output_hydro_table.merge(input_huc.loc[:,[FIM_ID,'HUC8']],how='left',on=FIM_ID)

    if output_flows.HydroID.dtype != 'str': output_flows.HydroID = output_flows.HydroID.astype(str)
    output_hydro_table = output_hydro_table.merge(output_flows.loc[:,['HydroID','LakeID']],how='left',on='HydroID')
    output_hydro_table['LakeID'] = output_hydro_table['LakeID'].astype(int)
    output_hydro_table = output_hydro_table.rename(columns={'HUC8':'HUC'})
    if output_hydro_table.HUC.dtype != 'str': output_hydro_table.HUC = output_hydro_table.HUC.astype(str)

    output_hydro_table.drop(columns=FIM_ID,inplace=True)
    if output_hydro_table.feature_id.dtype != 'int': output_hydro_table.feature_id = output_hydro_table.feature_id.astype(int)
    if output_hydro_table.feature_id.dtype != 'str': output_hydro_table.feature_id = output_hydro_table.feature_id.astype(str)

    # make src json
    output_src_json = dict()
    hydroID_list = unique(output_src['HydroID'])

    for hid in hydroID_list:
        indices_of_hid = output_src['HydroID'] == hid
        stage_list = output_src['Stage'][indices_of_hid].astype(float)
        q_list = output_src['Discharge (m3s-1)'][indices_of_hid].astype(float)

        stage_list = stage_list.tolist()
        q_list = q_list.tolist()

        output_src_json[str(hid)] = { 'q_list' : q_list , 'stage_list' : stage_list }

    # write out
    output_catchments.to_file(output_catchments_fileName,driver=getDriver(output_catchments_fileName),index=False)
    output_flows.to_file(output_flows_fileName,driver=getDriver(output_flows_fileName),index=False)
    output_src.to_csv(output_src_fileName,index=False)
    output_crosswalk.to_csv(output_crosswalk_fileName,index=False)
    output_hydro_table.to_csv(output_hydro_table_fileName,index=False)

    with open(output_src_json_fileName,'w') as f:
        json.dump(output_src_json,f,sort_keys=True,indent=2)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Crosswalk for MS/FR/GMS networks; calculate synthetic rating curves; update short rating curves')
    parser.add_argument('-d','--input-catchments-fileName', help='DEM derived catchments', required=True)
    parser.add_argument('-a','--input-flows-fileName', help='DEM derived streams', required=True)
    parser.add_argument('-s','--input-srcbase-fileName', help='Base synthetic rating curve table', required=True)
    parser.add_argument('-l','--output-catchments-fileName', help='Subset crosswalked catchments', required=True)
    parser.add_argument('-f','--output-flows-fileName', help='Subset crosswalked streams', required=True)
    parser.add_argument('-r','--output-src-fileName', help='Output crosswalked synthetic rating curve table', required=True)
    parser.add_argument('-j','--output-src-json-fileName',help='Output synthetic rating curve json',required=True)
    parser.add_argument('-x','--output-crosswalk-fileName',help='Crosswalk table',required=True)
    parser.add_argument('-t','--output-hydro-table-fileName',help='Hydrotable',required=True)
    parser.add_argument('-w','--input-huc-fileName',help='HUC8 boundary',required=True)
    parser.add_argument('-b','--input-nwmflows-fileName',help='Subest NWM burnlines',required=True)
    parser.add_argument('-y','--input-nwmcatras-fileName',help='NWM catchment raster',required=False)
    parser.add_argument('-m','--mannings-n',help='Mannings n. Accepts single parameter set or list of parameter set in calibration mode. Currently input as csv.',required=True)
    parser.add_argument('-z','--input-nwmcat-fileName',help='NWM catchment polygon',required=True)
    parser.add_argument('-p','--extent',help='GMS only for now', default='GMS', required=False)
    parser.add_argument('-k','--small-segments-filename',help='output list of short segments',required=True)
    parser.add_argument('-c','--calibration-mode',help='Mannings calibration flag',required=False,action='store_true')
    parser.add_argument('-e', '--min-catchment-area', help='Minimum catchment area', required=True)
    parser.add_argument('-g', '--min-stream-length', help='Minimum stream length', required=True)

    args = vars(parser.parse_args())

    add_crosswalk(**args)
   
