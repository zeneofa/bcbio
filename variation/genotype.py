"""High level parallel SNP and indel calling using multiple variant callers.
"""
import os
import collections
import copy

from bcbio import utils
from bcbio.distributed.split import grouped_parallel_split_combine
from bcbio.pipeline import region
from bcbio.variation import gatk, gatkfilter, multi, phasing, ploidy, vfilter

# ## Variant filtration -- shared functionality

def variant_filtration(call_file, ref_file, vrn_files, data):
    """Filter variant calls using Variant Quality Score Recalibration.

    Newer GATK with Haplotype calling has combined SNP/indel filtering.
    """
    caller = data["config"]["algorithm"].get("variantcaller")
    call_file = ploidy.filter_vcf_by_sex(call_file, data)
    if caller in ["freebayes"]:
        return vfilter.freebayes(call_file, ref_file, vrn_files, data)
    elif caller in ["gatk", "gatk-haplotype"]:
        return gatkfilter.run(call_file, ref_file, vrn_files, data)
    # no additional filtration for callers that filter as part of call process
    else:
        return call_file

# ## High level functionality to run genotyping in parallel

def get_variantcaller(data):
    if data.get("align_bam"):
        return data["config"]["algorithm"].get("variantcaller", "gatk")

def combine_multiple_callers(samples):
    """Collapse together variant calls from multiple approaches into single data item with `variants`.
    """
    by_bam = collections.OrderedDict()
    for data in (x[0] for x in samples):
        work_bam = utils.get_in(data, ("combine", "work_bam", "out"), data.get("align_bam"))
        variantcaller = get_variantcaller(data)
        key = (data["description"], work_bam)
        try:
            by_bam[key][variantcaller] = data
        except KeyError:
            by_bam[key] = {variantcaller: data}
    out = []
    for grouped_calls in [d.values() for d in by_bam.values()]:
        ready_calls = [{"variantcaller": get_variantcaller(x),
                        "vrn_file": x.get("vrn_file"),
                        "vrn_file_batch": x.get("vrn_file_batch"),
                        "validate": x.get("validate")}
                       for x in grouped_calls]
        final = grouped_calls[0]
        def orig_variantcaller_order(x):
            return final["config"]["algorithm"]["orig_variantcaller"].index(x["variantcaller"])
        if len(ready_calls) > 1 and "orig_variantcaller" in final["config"]["algorithm"]:
            final["variants"] = sorted(ready_calls, key=orig_variantcaller_order)
            final["config"]["algorithm"]["variantcaller"] = final["config"]["algorithm"].pop("orig_variantcaller")
        else:
            final["variants"] = ready_calls
        final.pop("vrn_file_batch", None)
        out.append([final])
    return out

def _split_by_ready_regions(ext, file_key, dir_ext_fn):
    """Organize splits based on regions generated by parallel_prep_region.
    """
    def _do_work(data):
        if "region" in data:
            name = data["group"][0] if "group" in data else data["description"]
            out_dir = os.path.join(data["dirs"]["work"], dir_ext_fn(data))
            out_file = os.path.join(out_dir, "%s%s" % (name, ext))
            assert isinstance(data["region"], (list, tuple))
            out_parts = []
            for i, r in enumerate(data["region"]):
                out_region_dir = os.path.join(out_dir, r[0])
                out_region_file = os.path.join(out_region_dir,
                                               "%s-%s%s" % (name, region.to_safestr(r), ext))
                work_bams = []
                for xs in data["region_bams"]:
                    if len(xs) == 1:
                        work_bams.append(xs[0])
                    else:
                        work_bams.append(xs[i])
                for work_bam in work_bams:
                    assert os.path.exists(work_bam), work_bam
                out_parts.append((r, work_bams, out_region_file))
            return out_file, out_parts
        else:
            return None, []
    return _do_work

def _collapse_by_bam_variantcaller(samples):
    """Collapse regions to a single representative by BAM input and variant caller.
    """
    by_bam = collections.OrderedDict()
    for data in (x[0] for x in samples):
        work_bam = utils.get_in(data, ("combine", "work_bam", "out"), data.get("align_bam"))
        variantcaller = get_variantcaller(data)
        if isinstance(work_bam, list):
            work_bam = tuple(work_bam)
        key = (data["description"], work_bam, variantcaller)
        try:
            by_bam[key].append(data)
        except KeyError:
            by_bam[key] = [data]
    out = []
    for grouped_data in by_bam.values():
        cur = grouped_data[0]
        cur.pop("region", None)
        region_bams = cur.pop("region_bams", None)
        if region_bams and len(region_bams[0]) > 1:
            cur.pop("work_bam", None)
        out.append([cur])
    return out

def parallel_variantcall_region(samples, run_parallel):
    """Perform variant calling and post-analysis on samples by region.
    """
    to_process = []
    extras = []
    for x in samples:
        added = False
        for add in handle_multiple_variantcallers(x):
            added = True
            to_process.append(add)
        if not added:
            extras.append(x)
    split_fn = _split_by_ready_regions(".vcf.gz", "work_bam", get_variantcaller)
    samples = _collapse_by_bam_variantcaller(
        grouped_parallel_split_combine(to_process, split_fn,
                                       multi.group_batches, run_parallel,
                                       "variantcall_sample", "concat_variant_files",
                                       "vrn_file", ["region", "sam_ref", "config"]))
    return extras + samples

def handle_multiple_variantcallers(data):
    """Split samples that potentially require multiple variant calling approaches.
    """
    assert len(data) == 1
    callers = get_variantcaller(data[0])
    if isinstance(callers, basestring):
        return [data]
    elif not callers:
        return []
    else:
        out = []
        for caller in callers:
            base = copy.deepcopy(data[0])
            base["config"]["algorithm"]["orig_variantcaller"] = \
              base["config"]["algorithm"]["variantcaller"]
            base["config"]["algorithm"]["variantcaller"] = caller
            out.append([base])
        return out

def get_variantcallers():
    from bcbio.variation import freebayes, cortex, samtools, varscan, mutect
    return {"gatk": gatk.unified_genotyper,
            "gatk-haplotype": gatk.haplotype_caller,
            "freebayes": freebayes.run_freebayes,
            "cortex": cortex.run_cortex,
            "samtools": samtools.run_samtools,
            "varscan": varscan.run_varscan,
            "mutect": mutect.mutect_caller}

def variantcall_sample(data, region=None, align_bams=None, out_file=None):
    """Parallel entry point for doing genotyping of a region of a sample.
    """
    if out_file is None or not os.path.exists(out_file) or not os.path.lexists(out_file):
        utils.safe_makedir(os.path.dirname(out_file))
        sam_ref = data["sam_ref"]
        config = data["config"]
        caller_fns = get_variantcallers()
        caller_fn = caller_fns[config["algorithm"].get("variantcaller", "gatk")]
        if len(align_bams) == 1:
            items = [data]
        else:
            items = multi.get_orig_items(data)
            assert len(items) == len(align_bams)
        call_file = "%s-raw%s" % utils.splitext_plus(out_file)
        call_file = caller_fn(align_bams, items, sam_ref,
                              data["genome_resources"]["variation"],
                              region, call_file)
        if data["config"]["algorithm"].get("phasing", False) == "gatk":
            call_file = phasing.read_backed_phasing(call_file, align_bams, sam_ref, region, config)
        utils.symlink_plus(call_file, out_file)
    if region:
        data["region"] = region
    data["vrn_file"] = out_file
    return [data]
