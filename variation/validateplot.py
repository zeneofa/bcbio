"""Plot validation results from variant calling comparisons.

Handles data normalization and plotting, emphasizing comparisons on methodology
differences.
"""
import collections
import os

import numpy as np
try:
    import prettyplotlib as ppl
    import pandas as pd
except ImportError:
    gg, pd, ppl = None, None, None

from bcbio import utils
from bcbio.variation import bamprep

def create_from_csv(in_csv, config=None, outtype="pdf", title=None, size=None):
    df = pd.read_csv(in_csv)
    create(df, None, 0, config or {}, os.path.splitext(in_csv)[0], outtype, title,
           size)

def create(plot_data, header, ploti, sample_config, out_file_base, outtype="pdf",
           title=None, size=None):
    """Create plots of validation results for a sample, labeling prep strategies.
    """
    if pd is None or ppl is None:
        return None
    if header:
        df = pd.DataFrame(plot_data, columns=header)
    else:
        df = plot_data
    df["aligner"] = [get_aligner(x, sample_config) for x in df["sample"]]
    df["bamprep"] = [get_bamprep(x, sample_config) for x in df["sample"]]
    floors = get_group_floors(df, cat_labels)
    df["value.floor"] = [get_floor_value(x, cat, vartype, floors)
                         for (x, cat, vartype) in zip(df["value"], df["category"], df["variant.type"])]
    out = []
    for i, prep in enumerate(df["bamprep"].unique()):
        out.append(plot_prep_methods(df, prep, i + ploti, out_file_base, outtype, title, size))
    return out

cat_labels = {"concordant": "Concordant",
              "discordant-missing-total": "Discordant (missing)",
              "discordant-extra-total": "Discordant (extra)",
              "discordant-shared-total": "Discordant (shared)"}
vtype_labels = {"snp": "SNPs", "indel": "Indels"}
prep_labels = {"gatk": "GATK best-practice BAM preparation (recalibration, realignment)",
               "none": "Minimal BAM preparation (samtools de-duplication only)"}
caller_labels = {"ensemble": "Ensemble", "freebayes": "FreeBayes",
                 "gatk": "GATK Unified\nGenotyper", "gatk-haplotype": "GATK Haplotype\nCaller"}

def plot_prep_methods(df, prep, prepi, out_file_base, outtype, title=None,
                      size=None):
    """Plot comparison between BAM preparation methods.
    """
    samples = df[(df["bamprep"] == prep)]["sample"].unique()
    assert len(samples) >= 1, samples
    out_file = "%s-%s.%s" % (out_file_base, samples[0], outtype)
    df = df[df["category"].isin(cat_labels)]
    _prettyplot(df, prep, prepi, out_file, title, size)
    return out_file

def _prettyplot(df, prep, prepi, out_file, title=None, size=None):
    """Plot using prettyplot wrapper around matplotlib.
    """
    cats = ["concordant", "discordant-missing-total",
            "discordant-extra-total", "discordant-shared-total"]
    vtypes = df["variant.type"].unique()
    fig, axs = ppl.subplots(len(vtypes), len(cats))
    callers = sorted(df["caller"].unique())
    width = 0.8
    for i, vtype in enumerate(vtypes):
        ax_row = axs[i] if len(vtypes) > 1 else axs
        for j, cat in enumerate(cats):
            ax = ax_row[j]
            if i == 0:
                ax.set_title(cat_labels[cat], size=14)
            ax.get_yaxis().set_ticks([])
            if j == 0:
                ax.set_ylabel(vtype_labels[vtype], size=14)
            vals, labels, maxval = _get_chart_info(df, vtype, cat, prep, callers)
            ppl.bar(ax, np.arange(len(callers)), vals,
                    color=ppl.colors.set2[prepi], width=width)
            ax.set_ylim(0, maxval)
            if i == len(vtypes) - 1:
                ax.set_xticks(np.arange(len(callers)) + width / 2.0)
                ax.set_xticklabels([caller_labels.get(x, x).replace("__", "\n") if x else ""
                                    for x in callers], size=8, rotation=45)
            else:
                ax.get_xaxis().set_ticks([])
            _annotate(ax, labels, vals, np.arange(len(callers)), width)
    fig.text(.5, .95, prep_labels[prep] if title is None else title, horizontalalignment='center', size=16)
    fig.subplots_adjust(left=0.05, right=0.95, top=0.87, bottom=0.15, wspace=0.1, hspace=0.1)
    #fig.tight_layout()
    x, y = (10, 5) if size is None else size
    fig.set_size_inches(x, y)
    fig.savefig(out_file)

def _get_chart_info(df, vtype, cat, prep, callers):
    """Retrieve values for a specific variant type, category and prep method.
    """
    maxval_raw = max(list(df["value.floor"]))
    curdf = df[(df["variant.type"] == vtype) & (df["category"] == cat)
               & (df["bamprep"] == prep)]
    vals = []
    labels = []
    for c in callers:
        row = curdf[df["caller"] == c]
        if len(row) > 0:
            vals.append(list(row["value.floor"])[0])
            labels.append(list(row["value"])[0])
        else:
            vals.append(1)
            labels.append("")
    return vals, labels, maxval_raw

def _annotate(ax, annotate, height, left, width):
    """Annotate axis with labels. Adjusted from prettyplotlib to be more configurable.
    """
    annotate_yrange_factor = 0.025
    xticks = np.array(left) + width / 2.0
    ymin, ymax = ax.get_ylim()
    yrange = ymax - ymin

    # Reset ymax and ymin so there's enough room to see the annotation of
    # the top-most
    if ymax > 0:
        ymax += yrange * 0.1
    if ymin < 0:
        ymin -= yrange * 0.1
    ax.set_ylim(ymin, ymax)
    yrange = ymax - ymin

    offset_ = yrange * annotate_yrange_factor
    if isinstance(annotate, collections.Iterable):
        annotations = map(str, annotate)
    else:
        annotations = ['%.3f' % h if type(h) is np.float_ else str(h)
                       for h in height]
    for x, h, annotation in zip(xticks, height, annotations):
        # Adjust the offset to account for negative bars
        offset = offset_ if h >= 0 else -1 * offset_
        verticalalignment = 'bottom' if h >= 0 else 'top'

        if len(str(annotation)) > 6:
            size = 7
        elif len(str(annotation)) > 5:
            size = 8
        else:
            size = 10
        # Finally, add the text to the axes
        ax.annotate(annotation, (x, h + offset),
                    verticalalignment=verticalalignment,
                    horizontalalignment='center',
                    size=size,
                    color=ppl.colors.almost_black)

def _ggplot(df, out_file):
    """Plot faceted items with ggplot wrapper on top of matplotlib.
    XXX Not yet functional
    """
    import ggplot as gg
    df["variant.type"] = [vtype_labels[x] for x in df["variant.type"]]
    df["category"] = [cat_labels[x] for x in df["category"]]
    df["caller"] = [caller_labels.get(x, None) for x in df["caller"]]
    p = (gg.ggplot(df, gg.aes(x="caller", y="value.floor")) + gg.geom_bar()
         + gg.facet_wrap("variant.type", "category")
         + gg.theme_seaborn())
    gg.ggsave(p, out_file)

def get_floor_value(x, cat, vartype, floors):
    """Modify values so all have the same relative scale for differences.

    Using the chosen base heights, adjusts an individual sub-plot to be consistent
    relative to that height.
    """
    all_base = floors[vartype]
    cur_max = floors[(cat, vartype)]
    if cur_max > all_base:
        diff = cur_max - all_base
        x = max(1, x - diff)
    return x

def get_group_floors(df, cat_labels):
    """Retrieve the floor for a given row of comparisons, creating a normalized set of differences.

    We need to set non-zero floors so large numbers (like concordance) don't drown out small
    numbers (like discordance). This defines the height for a row of comparisons as either
    the minimum height of any sub-plot, or the maximum difference between higher and lower
    (plus 10%).
    """
    group_maxes = collections.defaultdict(list)
    group_diffs = collections.defaultdict(list)
    diff_pad = 0.1  # 10% padding onto difference to avoid large numbers looking like zero
    for name, group in df.groupby(["category", "variant.type"]):
        label, stype = name
        if label in cat_labels:
            diff = max(group["value"]) - min(group["value"])
            group_diffs[stype].append(diff + int(diff_pad * diff))
            group_maxes[stype].append(max(group["value"]))
        group_maxes[name].append(max(group["value"]))
    out = {}
    for k, vs in group_maxes.iteritems():
        if k in group_diffs:
            out[k] = max(max(group_diffs[stype]), min(vs))
        else:
            out[k] = min(vs)
    return out

def get_aligner(x, config):
    return utils.get_in(config, ("algorithm", "aligner"), "")

def get_bamprep(x, config):
    params = bamprep._get_prep_params({"config": {"algorithm": config.get("algorithm", {})}})
    if params["realign"] == "gatk" and params["recal"] == "gatk":
        return "gatk"
    elif not params["realign"] and not params["recal"]:
        return "none"
    else:
        raise ValueError("Unexpected bamprep approach: %s" % params)
