#!/usr/bin/env python
# ccle_fetcher 0.0.1
# Generated by dx-app-wizard.

import dxpy
import subprocess
import os
import json
import time
import tempfile
import urllib
import xmltodict
import hashlib
import multiprocessing

@dxpy.entry_point("main")
def main(id_or_barcode):
    if len(id_or_barcode) == 0:
        raise dxpy.AppError("id_or_barcode must be the UUID or Barcode (aka legacy_sample_id) of a CCLE dataset")

    # look up the dataset
    info = ccle_index_lookup(id_or_barcode)
    # see if existing files in the project are available
    dxfiles = ccle_fetch_existing(info)
    if dxfiles is None:
        # otherwise download them
        dxfiles = ccle_gtdownload(info)

    # figure out which is the bam and which is the bai
    bams = [dxfile for dxfile in dxfiles if os.path.splitext(dxfile.describe()['name'])[1].lower() == '.bam']
    bais = [dxfile for dxfile in dxfiles if os.path.splitext(dxfile.describe()['name'])[1].lower() == '.bai']
    if len(dxfiles) != 2 or len(bams) != 1 or len(bais) != 1:
        raise AppInternalError("Unexpected: the dataset doesn't consist of exactly one BAM and one BAI file")
    bam = bams.pop()
    bai = bais.pop()

    return {
        'bam': dxpy.dxlink(bam.get_id()),
        'bai': dxpy.dxlink(bai.get_id())
    }

# Use cgquery to look up the info for a CCLE dataset by analysis_id. If lookup
# by analysis_id fails, attempt lookup by "Barcode" (aka legacy_sample_id)
def ccle_index_lookup(id_or_barcode):
    fd, fn = tempfile.mkstemp()
    os.close(fd)

    ans = retry(lambda: cgquery('analysis_id',id_or_barcode), 'cgquery')

    if ans is None:
        ans = retry(lambda: cgquery('legacy_sample_id',id_or_barcode), 'cgquery')
    
    if ans is None:
        raise dxpy.AppError("Could not find the CCLE dataset {} using cgquery. Ensure it's the analysis_id or legacy_sample_id (aka Barcode) of a live CCLE dataset. Note: CGHub has scheduled maintenance windows on Tuesdays and Thursdays from 1:00-5:00 PM Pacific.".format(id_or_barcode))
    if isinstance(ans, list):
        raise dxpy.AppInternalError("Unexpected: the analysis ID {} appears to match multiple CCLE datasets".format(id_or_barcode))

    if '@id' in ans and str(ans['@id']) == '1':
        return ans

    print ans
    raise dxpy.AppInternalError("Unexpected XML content from cgquery")    

# run cgquery with given key-value query
def cgquery(key,value):
    fd, fn = tempfile.mkstemp()
    os.close(fd)
    subprocess.check_call(['./cgquery', 'state=live&{}="{}"'.format(key,value), '-o', fn])
    # parse xml
    resultset = xmltodict.parse(open(fn))['ResultSet']
    return resultset['Result'] if 'Result' in resultset else None

# Given the metadata of a CCLE dataset from the index, see if all its
# constituent files are already present in the project. If so, return a list
# of DXFiles; otherwise return None.
def ccle_fetch_existing(info):
    analysis_id = str(info['analysis_id'])
    expected_files = ccle_expected_files(info)
    print '\n\nLooking for existing data for {} in the project, consisting of files: {}'.format(analysis_id,json.dumps(expected_files))

    # for each expected file, see if it's already in the project
    existing = []
    for md5 in expected_files:
        for candidate in dxpy.find_data_objects(project=dxpy.PROJECT_CONTEXT_ID,
                                                classname='file',
                                                state='closed',
                                                name=expected_files[md5],
                                                name_mode='exact',
                                                properties={'md5': md5},
                                                return_handler=True):
            deets = candidate.get_details()
            if 'cghub_metadata' in deets and 'md5' in deets and deets['md5'] == md5:
                existing.append(candidate)
                break

    # if the project already has all of them, we can quit early
    if len(existing) == len(expected_files):
        print 'The files are already in the project!'
        dxpy.DXProject(dxpy.PROJECT_CONTEXT_ID).clone(dxpy.WORKSPACE_ID,objects=[dxfile.get_id() for dxfile in existing])
        return existing
    elif len(existing) > 0:
        print 'Only some of the files are already in the project!'
    else:
        print 'No existing data found in the project.'

    return None

# gtdownload and import one CCLE dataset, given its metadata entry from the
# index. Return a list of DXFiles
_installed_genetorrent=False
def ccle_gtdownload(info):
    global _installed_genetorrent
    if not _installed_genetorrent:
        sh("dpkg -i genetorrent-common.deb")
        sh("dpkg -i genetorrent-download.deb")
        _installed_genetorrent = True

    # perform the download to local scratch space
    analysis_id = str(info['analysis_id'])
    expected_files = ccle_expected_files(info)
    print 'Downloading {}, consisting of files: {}'.format(analysis_id,json.dumps(expected_files))
    t00 = time.time()
    try:
        sh("gtdownload -c https://cghub.ucsc.edu/software/downloads/cghub_public.key --max-children {} -d {}".format(multiprocessing.cpu_count(), analysis_id))
    except:
        raise dxpy.AppError("Failed to download the data from CGHub using GeneTorrent. Check the job log for more details. Note: CGHub has scheduled maintenance windows on Tuesdays and Thursdays from 1:00-5:00 PM Pacific.")
    t10 = time.time()

    # validate the files gtdownload placed in the expected subdirectory
    if not os.path.isdir(analysis_id):
        raise dxpy.AppInternalError("Unexpected: GeneTorrent gtdownload did not create a subdirectory for " + analysis_id)
    print 'Verifying gtdownload products'
    products = []
    total_file_size = 0
    for dirname, subdirs, filenames in os.walk(analysis_id):
        if len(subdirs) > 0:
            raise dxpy.AppInternalError("Unexpected: GeneTorrent gtdownload created subdirectories " + subdirs)
        for filename in filenames:
            filepath = os.path.join(dirname, filename)
            md5 = md5sum(filepath)
            print "{} {}".format(md5,filename)
            if md5 not in expected_files:
                raise dxpy.AppInternalError("GeneTorrent gtdownload produced a file {} with MD5 {} which does not match a file in the CCLE index for {}".format(filename, md5, analysis_id))
            if str(filename) != str(expected_files[md5]):
                raise dxpy.AppInternalError("GeneTorrent gtdownload produced a file {} but the expected name was {} based on the CCLE index for {}".format(filename, expected_files[md5], analysis_id))
            del expected_files[md5]
            products.append((md5, filepath))
            total_file_size += os.path.getsize(filepath)

    # make sure we got everything
    if len(expected_files) > 0:
        raise dxpy.AppInternalError("GeneTorrent gtdownload did not produce all expected files for {}. Missing: {}".format(analysis_id, expected_files))
    t20 = time.time()

    # upload to platform
    print 'Uploading to platform'
    dxfiles = []
    for (md5,filename) in products:
        dxfile = dxpy.upload_local_file(filename,keep_open=True)

        # store some metadata
        dxfile.set_details({'md5': md5, 'cghub_metadata': info})
        dxfile.set_properties({'md5': md5, 'analysis_id': analysis_id, 'legacy_sample_id': str(info['legacy_sample_id'])})
        dxfile.add_tags(['from_ccle_fetcher'])

        # add file to the list of output files
        dxfile.close()
        dxfiles.append(dxfile)
    t30 = time.time()

    print '\t'.join(['performance', str(total_file_size), str(int(round(t10-t00))), str(int(round(t20-t10))), str(int(round(t30-t20)))])

    return dxfiles

# given one entry from the CCLE index, make a (md5 -> filename) dict for each
# constituent file
def ccle_expected_files(info):
    analysis_id = str(info['analysis_id'])
    expected_files = {}
    for expected_file in info['files']['file']:
        if 'checksum' not in expected_file or '@type' not in expected_file['checksum'] or str(expected_file['checksum']['@type']).lower() != "md5" or "#text" not in expected_file['checksum'] or len(expected_file['checksum']['#text']) == 0:
            raise dxpy.AppInternalError("Could not extract file MD5 checksums from CGHub index metadata for {}".format(analysis_id))
        if 'filename' not in expected_file:
            raise dxpy.AppInternalError("Could not extract filename from CGHub index metadata for {}".format(analysis_id))
        expected_files[str(expected_file['checksum']["#text"])] = str(expected_file['filename'])
    return expected_files

def sh(cmd):
    subprocess.check_call(cmd, shell=True)

# http://stackoverflow.com/questions/1131220/get-md5-hash-of-big-files-in-python
def md5sum(filename):
    md5 = hashlib.md5()
    with open(filename,'rb') as f: 
        for chunk in iter(lambda: f.read(128*md5.block_size), b''): 
             md5.update(chunk)
    return md5.hexdigest()

def retry(f, caption='operation', retries=4, backoff=1):
    try:
        return f()
    except:
        if retries <= 0:
            raise
        else:
            time.sleep(backoff)
            print 'retry: {}'.format(caption)
            retry(f, caption, retries-1, backoff*2)

dxpy.run()
