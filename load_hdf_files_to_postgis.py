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


def reproject_to_lonlat(source_dataset):
    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(4326)
    dst_wkt = dst_srs.ExportToWkt()

    error_threshold = 0.125  # error threshold --> use same value as in gdalwarp
    resampling = gdal.GRA_NearestNeighbour

    reprojected_dataset = gdal.AutoCreateWarpedVRT(source_dataset,
                                      None,  # src_wkt : left to default value --> will use the one from source
                                      dst_wkt,
                                      resampling,
                                      error_threshold)
    return reprojected_dataset


def process(hdf_file, target_table_name, year):
    conn_string = "dbname='londonwald' user='carolinux'"
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()

    # extract box coordinates for every 250x250m tree covered box
    hdf_dataset = gdal.Open(hdf_file)
    tree_cover_dataset_name = hdf_dataset.GetSubDatasets()[TREE_COVER_DATASET_INDEX_WITHIN_HDF_FILE][0]
    tree_cover_dataset = gdal.Open(tree_cover_dataset_name, gdal.GA_ReadOnly)
    tree_cover_dataset = reproject_to_lonlat(tree_cover_dataset)
    geotransform = tree_cover_dataset.GetGeoTransform()
    band = tree_cover_dataset.GetRasterBand(1)
    band_types_map = {'Byte': 'B', 'UInt16': 'H', 'Int16': 'h', 'UInt32': 'I',
                      'Int32': 'i', 'Float32': 'f', 'Float64': 'd'}
    band_type = gdal.GetDataTypeName(band.DataType)
    topleftX = geotransform[0]  # top left x
    topleftY = geotransform[3]  # top left y
    X = topleftX
    Y = topleftY
    stepX = geotransform[1]
    stepY = geotransform[5]

    insert_sql = 'INSERT INTO {} (box, year, tree_cover) VALUES %s'.format(target_table_name)
    i = 0
    inserted = 0
    for y in range(band.YSize):

        scanline = band.ReadRaster(0, y, band.XSize, 1, band.XSize, 1, band.DataType)
        tree_cover_values = struct.unpack(band_types_map[band_type] * band.XSize, scanline)
        tuples = []

        for j, tree_cover_value in enumerate(tree_cover_values):

            if polygon_intersects_with_bounding_box(X, Y, stepX, stepY):

                ewkt = 'SRID=4326;POLYGON(({} {}, {} {}, {} {},{} {}, {} {}))'.format(
                    X, Y,
                    X + stepX, Y,
                    X + stepX, Y + stepY,
                    X, Y + stepY,
                    X, Y)

                tup = (ewkt, year, tree_cover_value)
                tuples.append(tup)

            i += 1
            X += stepX

        if len(tuples) > 0:
            execute_values(cur, insert_sql, tuples)
            conn.commit()
            inserted += len(tuples)
        X = topleftX
        Y += stepY

    cur.close()
    conn.close()
    print("Found {} forest boxes".format(inserted))


def polygon_intersects_with_bounding_box(x, y, stepX, stepY):
    return point_within_bounding_box(x, y) or point_within_bounding_box(x + stepX, y) or\
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
    conn_string = "dbname='londonwald' user='carolinux'"
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS {}
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
    forest_boxes_table_name = 'forest_boxes2'
    create_table_for_forest_boxes(forest_boxes_table_name)
    for fn in os.listdir('./data'):
        if not fn.endswith('hdf'):
            continue
        hdf_file = os.path.join('data', fn)
        year_captured = extract_date_captured_from_hdf_file(hdf_file)
        process(hdf_file, forest_boxes_table_name, year_captured)
