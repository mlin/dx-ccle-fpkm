#!/usr/bin/env python
#
# Copyright (C) 2013 DNAnexus, Inc.
#   This file is part of dnanexus-example-applets.
#   You may use this file under the terms of the Apache License, Version 2.0;
#   see the License.md file for more information.


# tophat_cufflinks_pipeline 0.0.1
# Generated by dx-app-wizard.
#
# Basic execution pattern: Your app will run on a single machine from
# beginning to end.
#
# See http://wiki.dnanexus.com/Developer-Tutorials/Intro-to-Building-Apps
# for instructions on how to modify this file.
#
# DNAnexus Python Bindings (dxpy) documentation:
#   http://autodoc.dnanexus.com/bindings/python/current/

import os
import dxpy
import subprocess
import multiprocessing

num_cpus = multiprocessing.cpu_count()

@dxpy.entry_point('main')
def main(bam, genes, cufflinks_options="", name=None):

    bam = dxpy.DXFile(bam)
    genes = dxpy.DXFile(genes)

    if name is None:
        name = os.path.splitext(bam.describe()['name'])[0]

    # stage mappings and genes to scratch space
    dxpy.download_dxfile(bam.get_id(), "mappings.bam")
    sh("dx-genes-to-gtf --output genes.gtf " + genes.get_id())

    # run cufflinks
    sh("find . -type f")
    cufflinks_cmd ="cufflinks -p {} -G genes.gtf -o cufflinks_out {} mappings.bam".format(num_cpus,cufflinks_options)
    print cufflinks_cmd
    sh(cufflinks_cmd)
    sh("find . -type f")

    # collect output files
    output_files = []
    for dirname, subdirs, filenames in os.walk('cufflinks_out'):
        if len(subdirs) > 0:
            raise dxpy.AppInternalError("Unexpected: cufflinks created subdirectories " + subdirs)
        for filename in filenames:
            # rename to include the desired name
            if len(name)>0:
                newpath = os.path.join(dirname, "{}.{}".format(name, filename))
                sh("mv {} {}".format(os.path.join(dirname, filename), newpath))
            else:
                newpath = os.path.join(dirname, filename)
            # compress if more than 64MB
            if os.path.getsize(newpath) > 67108864:
                sh("gzip "+newpath)
                newpath += '.gz'
            # upload to platform
            output_files.append(dxpy.upload_local_file(newpath))

    return {'cufflinks_outputs': [dxpy.dxlink(dxfile) for dxfile in output_files]}

def sh(cmd):
    subprocess.check_call(cmd, shell=True)

dxpy.run()
