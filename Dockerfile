FROM  python:3.6

RUN apt-get update \
    && apt-get install -y \
         python3-gdal libpq-dev libgdal-dev gdal-bin

ENV LANG=C.UTF-8

RUN pip3 install psycopg2

RUN pip3 install GDAL==2.1.3 --global-option=build_ext --global-option="-I/usr/include/gdal"

WORKDIR /usr/src
