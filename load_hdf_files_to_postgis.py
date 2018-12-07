import os
import struct

from osgeo import gdal, osr
import psycopg2
from psycopg2.extras import execute_values

# bounding box for Greater London Area
# from https://wiki.openstreetmap.org/wiki/Bounding_Box
# In spatial databases X corresponds to lon and Y to lat
MIN_X = -0.5103751
MAX_X = 0.3340155
MIN_Y = 51.2867602
MAX_Y = 51.6918741

TREE_COVER_DATASET_INDEX_WITHIN_HDF_FILE = 0
BAND_TYPES_MAP = {'Byte': 'B', 'UInt16': 'H', 'Int16': 'h', 'UInt32': 'I',
                  'Int32': 'i', 'Float32': 'f', 'Float64': 'd'}
HDF_FOLDER = 'data'


def load_hdf_file_to_postgis_db(hdf_file, target_table_name, year):
    # extract the tree cover dataset from hdf file
    hdf_dataset = gdal.Open(hdf_file)
    tree_cover_dataset_name = hdf_dataset.GetSubDatasets()[TREE_COVER_DATASET_INDEX_WITHIN_HDF_FILE][0]
    tree_cover_dataset = gdal.Open(tree_cover_dataset_name, gdal.GA_ReadOnly)

    # reproject to lonlat
    tree_cover_dataset = reproject_to_lonlat(tree_cover_dataset)

    # extract info needed to create boxes
    band = tree_cover_dataset.GetRasterBand(1)
    band_type = gdal.GetDataTypeName(band.DataType)

    geotransform = tree_cover_dataset.GetGeoTransform()
    topleftX = geotransform[0]
    topleftY = geotransform[3]
    stepX = geotransform[1]
    stepY = geotransform[5]
    X = topleftX
    Y = topleftY

    # prepare db
    conn, cur = get_pg_connection_and_cursor()
    insert_sql_statement_template = 'INSERT INTO {} (box, year, tree_cover) VALUES %s'.format(target_table_name)

    # loop through grid
    for y in range(band.YSize):
        scanline = band.ReadRaster(0, y, band.XSize, 1, band.XSize, 1, band.DataType)
        tree_cover_values = struct.unpack(BAND_TYPES_MAP[band_type] * band.XSize, scanline)
        tuples = []

        for tree_cover_value in tree_cover_values:
            if intersects_with_greater_london_area_bounding_box(X, Y, stepX, stepY):
                ewkt = get_well_known_text_for_box_geometry(X, Y, stepX, stepY)
                tuples.append((ewkt, year, tree_cover_value))
            X += stepX

        X = topleftX
        Y += stepY
        if len(tuples) > 0:
            # execute_values can insert multiple rows at once, faster than doing it one by one
            execute_values(cur, insert_sql_statement_template, tuples)

    conn.commit()
    cur.close()
    conn.close()


def get_pg_connection_and_cursor():
    conn_string = "dbname='londonwald' user='carolinux' host='postgres' password='ilikeforests'"
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    return conn, cur


def reproject_to_lonlat(source_dataset):
    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(4326)
    dst_wkt = dst_srs.ExportToWkt()

    error_threshold = 0.125
    resampling = gdal.GRA_NearestNeighbour

    reprojected_dataset = gdal.AutoCreateWarpedVRT(source_dataset,
                                                   None,
                                                   # src_wkt : left to default value in order to use the one from source
                                                   dst_wkt,
                                                   resampling,
                                                   error_threshold)
    return reprojected_dataset


def get_well_known_text_for_box_geometry(X, Y, stepX, stepY):
    ewkt = 'SRID=4326;POLYGON(({} {}, {} {}, {} {},{} {}, {} {}))'.format(
        X, Y,
        X + stepX, Y,
        X + stepX, Y + stepY,
        X, Y + stepY,
        X, Y)
    return ewkt


def intersects_with_greater_london_area_bounding_box(x, y, stepX, stepY):
    return point_within_bounding_box(x, y) or point_within_bounding_box(x + stepX, y) or \
           point_within_bounding_box(x, y + stepY) or point_within_bounding_box(x + stepX, y + stepY)


def point_within_bounding_box(x, y):
    return MIN_X <= x <= MAX_X and MIN_Y <= y <= MAX_Y


def extract_date_captured_from_hdf_file(hdf_file):
    dataset = gdal.Open(hdf_file)
    metadata = dataset.GetMetadata_Dict()
    date_str = metadata['RANGEENDINGDATE']
    year = int(date_str[:4])
    return year


def create_table_for_forest_boxes(target_table_name):
    conn, cur = get_pg_connection_and_cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS {}
    (
      box geometry,
      tree_cover double precision,
      year integer
    );
    """.format(target_table_name))

    cur.execute("""
    CREATE INDEX IF NOT EXISTS {}_geom_idx
    ON {}
    USING gist
    (box);
    """.format(target_table_name, target_table_name))

    conn.commit()
    conn.close()


if __name__ == '__main__':
    forest_boxes_table_name = 'forest_boxes'
    print("Creating table")
    create_table_for_forest_boxes(forest_boxes_table_name)
    for fn in os.listdir(HDF_FOLDER):
        if not fn.endswith('hdf'):
            continue
        print("Processing file {}".format(fn))
        hdf_file = os.path.join(HDF_FOLDER, fn)
        year_captured = extract_date_captured_from_hdf_file(hdf_file)
        load_hdf_file_to_postgis_db(hdf_file, forest_boxes_table_name, year_captured)
    print("All done. Run 'psql' to connect to the db from here!")
