"""GATK variant calling -- HaplotypeCaller and UnifiedGenotyper.
"""
from distutils.version import LooseVersion

from bcbio import bam, broad, utils
from bcbio.distributed.transaction import file_transaction
from bcbio.pipeline.shared import subset_variant_regions
from bcbio.variation.realign import has_aligned_reads
from bcbio.variation import annotation, bamprep, ploidy, vcfutils

def _shared_gatk_call_prep(align_bams, items, ref_file, dbsnp, region, out_file):
    """Shared preparation work for GATK variant calling.
    """
    config = items[0]["config"]
    broad_runner = broad.runner_from_config(config)
    broad_runner.run_fn("picard_index_ref", ref_file)
    for x in align_bams:
        bam.index(x, config)
    # GATK can only downsample to a minimum of 200
    coverage_depth_max = max(200, utils.get_in(config, ("algorithm", "coverage_depth_max"), 10000))
    coverage_depth_min = utils.get_in(config, ("algorithm", "coverage_depth_min"), 4)
    variant_regions = config["algorithm"].get("variant_regions", None)
    confidence = "4.0" if coverage_depth_min < 4 else "30.0"
    region = subset_variant_regions(variant_regions, region, out_file, items)

    params = ["-R", ref_file,
              "--standard_min_confidence_threshold_for_calling", confidence,
              "--standard_min_confidence_threshold_for_emitting", confidence,
              "--downsample_to_coverage", str(coverage_depth_max),
              "--downsampling_type", "BY_SAMPLE",
              ]
    for a in annotation.get_gatk_annotations(config):
        params += ["--annotation", a]
    for x in align_bams:
        params += ["-I", x]
    if dbsnp:
        params += ["--dbsnp", dbsnp]
    if region:
        params += ["-L", bamprep.region_to_gatk(region), "--interval_set_rule", "INTERSECTION"]
    return broad_runner, params

def unified_genotyper(align_bams, items, ref_file, assoc_files,
                       region=None, out_file=None):
    """Perform SNP genotyping on the given alignment file.
    """
    if out_file is None:
        out_file = "%s-variants.vcf.gz" % utils.splitext_plus(align_bams[0])[0]
    if not utils.file_exists(out_file):
        config = items[0]["config"]
        broad_runner, params = \
            _shared_gatk_call_prep(align_bams, items, ref_file, assoc_files["dbsnp"],
                                   region, out_file)
        if (not isinstance(region, (list, tuple)) and
                not all(has_aligned_reads(x, region) for x in align_bams)):
            vcfutils.write_empty_vcf(out_file, config)
        else:
            with file_transaction(out_file) as tx_out_file:
                params += ["-T", "UnifiedGenotyper",
                           "-o", tx_out_file,
                           "-ploidy", (str(ploidy.get_ploidy(items, region))
                                       if broad_runner.gatk_type() == "restricted" else "2"),
                           "--genotype_likelihoods_model", "BOTH"]
                broad_runner.run_gatk(params)
    return out_file

def haplotype_caller(align_bams, items, ref_file, assoc_files,
                       region=None, out_file=None):
    """Call variation with GATK's HaplotypeCaller.

    This requires the full non open-source version of GATK.
    """
    if out_file is None:
        out_file = "%s-variants.vcf.gz" % utils.splitext_plus(align_bams[0])[0]
    if not utils.file_exists(out_file):
        config = items[0]["config"]
        broad_runner, params = \
            _shared_gatk_call_prep(align_bams, items, ref_file, assoc_files["dbsnp"],
                                   region, out_file)
        assert broad_runner.gatk_type() == "restricted", \
            "Require full version of GATK 2.4+ for haplotype calling"
        if not all(has_aligned_reads(x, region) for x in align_bams):
            vcfutils.write_empty_vcf(out_file, config)
        else:
            with file_transaction(out_file) as tx_out_file:
                params += ["-T", "HaplotypeCaller",
                           "-o", tx_out_file,
                           "--annotation", "ClippingRankSumTest",
                           "--annotation", "DepthPerSampleHC"]
                # Enable hardware based optimizations in GATK 3.1+
                if LooseVersion(broad_runner.gatk_major_version()) >= LooseVersion("3.1"):
                    params += ["--pair_hmm_implementation", "VECTOR_LOGLESS_CACHING"]
                broad_runner.new_resources("gatk-haplotype")
                broad_runner.run_gatk(params)
    return out_file
