#!/usr/bin/env python

import csv
import re
import argparse
import os
import subprocess
import logging
import sys
import pcgrutils
import pandas as np
import cyvcf
from cyvcf2 import VCF

def __main__():
   
   parser = argparse.ArgumentParser(description='Verify input data for PCGR')
   parser.add_argument('pcgr_dir',help='Docker location of PCGR base directory with accompanying data directory, e.g. /data')
   parser.add_argument('input_vcf', help='VCF input file with somatic query variants (SNVs/InDels)')
   parser.add_argument('input_cna_segments', help='Somatic copy number query segments (tab-separated values)')   
   args = parser.parse_args()
   
   ret = verify_pcgr_input(args.pcgr_dir, args.input_vcf, args.input_cna_segments)
   if ret != 0:
      sys.exit(-1)

def is_valid_cna_segment_file(cna_segment_file, logger):
   """
   Function that checks whether the CNA segment file adheres to the correct format
   """
   cna_reader = csv.DictReader(open(cna_segment_file,'r'), delimiter='\t')
   if not ('Chromosome' in cna_reader.fieldnames and 'Segment_Mean' in cna_reader.fieldnames and 'Start' in cna_reader.fieldnames and 'End' in cna_reader.fieldnames): ## check that required columns are present
      error_message_cnv = "Copy number segment file (" + str(cna_segment_file) + ") is missing required column(s): 'Chromosome', 'Start', 'End', or  'Segment_Mean'"
      error_message_cnv2 = "Column names present in file: " + str(cna_reader.fieldnames)
      logger.error('')
      logger.error(error_message_cnv)
      logger.error(error_message_cnv2)
      logger.error('')
      return -1
   
   cna_dataframe = np.read_csv(cna_segment_file, sep="\t")
   if not cna_dataframe['Start'].dtype.kind in 'i': ## check that 'Start' is of type integer
      logger.error('')
      logger.error('\'Start\' column of copy number segment file contains non-integer values')
      logger.error('')
      return -1
   if not cna_dataframe['End'].dtype.kind in 'i': ## check that 'End' is of type integer
      logger.error('')
      logger.error('\'End\' column of copy number segment file contains non-integer values')
      logger.error('')
      return -1
   if not cna_dataframe['Segment_Mean'].dtype.kind in 'if': ## check that 'Segment_Mean' is of type integer/float
      logger.error('')
      logger.error('\'Segment_Mean\' column of copy number segment file contains non-numerical values')
      logger.error('')
      return -1

   for rec in cna_reader:
      if int(rec['End']) < int(rec['Start']): ## check that 'End' is always greather than 'Start'
         logger.error('')
         logger.error('Detected wrongly formatted chromosomal segment - \'Start\' is greater than \'End\' (' + str(rec['Chromosome']) + ':' + str(rec['Start']) + '-' + str(rec['End']) + ')')
         logger.error('')
         return -1
      if rec['End'] < 1 or rec['Start'] < 1: ## check that 'Start' and 'End' is always non-negative
         logger.error('')
         logger.error('Detected wrongly formatted chromosomal segment - \'Start\' or \'End\' is less than or equal to zero (' + str(rec['Chromosome']) + ':' + str(rec['Start']) + '-' + str(rec['End']) + ')')
         logger.error('')
         return -1
   logger.info('Copy number segment file (' + str(cna_segment_file) + ') adheres to the correct format')
   return 0



def is_valid_vcf(vcf_validator_output_file):
   """
   Function that reads the output file of EBIvariation/vcf-validator and reports potential errors and validation status 
   """
   valid_vcf = -1
   ret = {}
   if os.path.exists(vcf_validator_output_file):
      f = open(vcf_validator_output_file,'r')
      error_messages = []
      for line in f:
         if not re.search(r' \(warning\)$|^Reading from ',line.rstrip()): ## ignore warnings
            if line.startswith('Line '):
               error_messages.append(line.rstrip())
            if line.endswith('the input file is valid'): ## valid VCF
               valid_vcf = 1
            if line.endswith('the input file is not valid'):  ## non-valid VCF
               valid_vcf = 0
      f.close()
      os.system('rm -f ' + str(vcf_validator_output_file))
      ret['error_messages'] = error_messages
      ret['validation_status'] = valid_vcf
   return ret


def check_existing_vcf_info_tags(input_vcf, pcgr_directory, logger):
   
   """
   Function that compares the INFO tags in the query VCF and the INFO tags generated by PCGR
   If any coinciding tags, an error will be returned
   """
   
   vep_infotags_desc = pcgrutils.read_infotag_file(os.path.join(pcgr_directory,'data','vep_infotags.tsv'))
   pcgr_infotags_desc = pcgrutils.read_infotag_file(os.path.join(pcgr_directory,'data','pcgr_infotags.tsv'))

   vcfanno_tags = {}
   for db in ['intogen_driver_mut','dbsnp','oneKG','docm','exac','gnomad','civic','cbmdb','dbnsfp','clinvar','icgc','cosmic']:
      vcfanno_tag_file = os.path.join(pcgr_directory,'data',str(db),str(db) + '.vcfanno.vcf_info_tags.txt')
      try:
         f = open(vcfanno_tag_file, 'r')
         for line in f:
            if line.startswith('##INFO'):
               tag = re.sub(r'##INFO=<ID=','',str(line.rstrip().split(',')[0]))
               vcfanno_tags[tag] = 1
      except IOError:
         logger.error('File ' + str(vcfanno_tag_file) + ' does not exist')
         
   vcf_reader = cyvcf.Reader(open(input_vcf, 'r'))
   logger.info('Checking if existing INFO tags of query VCF file coincide with PCGR INFO tags')
   ret = 1
   for k in vcf_reader.infos.keys():
      if k in vep_infotags_desc.keys() or k in pcgr_infotags_desc.keys() or k in vcfanno_tags.keys() or k == 'EFFECT_PREDICTIONS':
         if k != 'STRAND':
            logger.error('INFO tag ' + str(k) + ' in the query VCF coincides with a VCF annotation tag produced by PCGR - please remove or rename this tag in your query VCF')
            ret = -1
   return ret

def verify_pcgr_input(pcgr_directory, input_vcf, input_cna_segments):
   """
   Function that reads the input files to PCGR (VCF file and Tab-separated values file with copy number segments) and performs the following checks:
   1. Check that VCF file is properly formatted (according to EBIvariation/vcf-validator - VCF v4.2)
   2. Check that no INFO annotation tags in the query VCF coincides with those generated by PCGR
   3. Check that 'chr' is stripped from CHROM column in VCF file
   4. Check that no variants have multiple alternative alleles (e.g. 'A,T')
   5. Check that copy number segment file has required columns and correct data types (and range)
   6. Any genotype data from VCF input file is stripped, and the resulting VCF file is sorted and indexed (bgzip + tabix) 
   """
   logger = pcgrutils.getlogger('pcgr-check-input')
   input_vcf_pcgr_ready = '/workdir/output/' + re.sub(r'(\.vcf$|\.vcf\.gz$)','.pcgr_ready.vcf',os.path.basename(input_vcf))
   
   if not input_vcf == 'None':
      logger.info('Validating VCF file with EBIvariation/vcf-validator')
      vcf_validation_output_file = '/workdir/output/' + re.sub(r'(\.vcf$|\.vcf\.gz$)','.vcf_validator_output',os.path.basename(input_vcf))
      command_v42 = 'vcf_validator --input ' + str(input_vcf) + ' --version v4.2 > ' + str(vcf_validation_output_file)
      if input_vcf.endswith('.gz'):
         command_v42 = 'bgzip -dc ' + str(input_vcf) + ' | vcf_validator --version v4.2 > ' + str(vcf_validation_output_file)
      
      os.system(command_v42)
      validation_results = is_valid_vcf(vcf_validation_output_file)
      
      if not validation_results['validation_status']:
         error_string_42 = '\n'.join(validation_results['error_messages'])
         validation_status = 'VCF file is NOT valid according to v4.2 specification'
         logger.error(validation_status + ':\n' + str(error_string_42))
         return -1
      else:
         validation_status = 'VCF file ' + str(input_vcf) + ' is valid according to v4.2 specification'
         logger.info(validation_status)
      
      tag_check = check_existing_vcf_info_tags(input_vcf, pcgr_directory, logger)
      if tag_check == -1:
         return -1
      else:
         logger.info('No query VCF INFO tags coincide with PCGR INFO tags')
      
      if validation_results['validation_status']:
         multiallelic_alt = 0
         vcf = VCF(input_vcf)
         for rec in vcf:
            chrom = rec.CHROM
            if chrom.startswith('chr'):
               error_message_chrom = "'chr' must be stripped from chromosome names: " + str(rec.CHROM + ", see http://pcgr.readthedocs.io/en/latest/output.html#vcf-preprocessing")
               logger.error(error_message_chrom)
               return -1
            POS = rec.start + 1
            alt = ",".join(str(n) for n in rec.ALT)
            if len(rec.ALT) > 1:
               logger.error('')
               logger.error("Multiallelic site detected:" + str(rec.CHROM) + '\t' + str(POS) + '\t' + str(rec.REF) + '\t' + str(alt))
               logger.error('Alternative alleles must be decomposed, see http://pcgr.readthedocs.io/en/latest/output.html#vcf-preprocessing')
               logger.error('')
               multiallelic_alt = 1
               return -1
         command_vcf_sample_free1 = 'egrep \'^##\' ' + str(input_vcf) + ' > ' + str(input_vcf_pcgr_ready)
         command_vcf_sample_free2 = 'egrep \'^#CHROM\' ' + str(input_vcf) + ' | cut -f1-8 >> ' + str(input_vcf_pcgr_ready)
         command_vcf_sample_free3 = 'egrep -v \'^#\' ' + str(input_vcf) + ' | cut -f1-8 | egrep -v \'^[XYM]\' | sort -k1,1n -k2,2n -k3,3 -k4,4 >> ' + str(input_vcf_pcgr_ready)
         command_vcf_sample_free4 = 'egrep -v \'^#\' ' + str(input_vcf) + ' | cut -f1-8 | egrep \'^[XYM]\' | sort -k1,1 -k2,2n -k3,3 -k4,4 >> ' + str(input_vcf_pcgr_ready)
         if input_vcf.endswith('.gz'):
            command_vcf_sample_free1 = 'bgzip -dc ' + str(input_vcf) + ' | egrep \'^##\' > ' + str(input_vcf_pcgr_ready)
            command_vcf_sample_free2 = 'bgzip -dc ' + str(input_vcf) + ' | egrep \'^#CHROM\' | cut -f1-8 >> ' + str(input_vcf_pcgr_ready)
            command_vcf_sample_free3 = 'bgzip -dc ' + str(input_vcf) + ' | egrep -v \'^#\' | cut -f1-8 | egrep -v \'^[XYM]\' | sort -k1,1n -k2,2n -k3,3 -k4,4 >> ' + str(input_vcf_pcgr_ready)
            command_vcf_sample_free4 = 'bgzip -dc ' + str(input_vcf) + ' | egrep -v \'^#\' | cut -f1-8 | egrep \'^[XYM]\' | sort -k1,1 -k2,2n -k3,3 -k4,4 >> ' + str(input_vcf_pcgr_ready)
         os.system(command_vcf_sample_free1)
         os.system(command_vcf_sample_free2)
         os.system(command_vcf_sample_free3)
         os.system(command_vcf_sample_free4)
         os.system('bgzip -f ' + str(input_vcf_pcgr_ready))
         os.system('tabix -p vcf ' + str(input_vcf_pcgr_ready) + '.gz')
      
   if not input_cna_segments == 'None':
      ret = is_valid_cna_segment_file(input_cna_segments, logger)
      return ret
   
   return 0
   
if __name__=="__main__": __main__()

