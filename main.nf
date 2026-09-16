#!/usr/bin/env nextflow

nextflow.enable.dsl = 2

include { RUN_VALIS }      from './modules/run_valis/main.nf'
include { SUMMARY_REPORT } from './modules/summary_report/main.nf'

def helpMessage() {
    log.info """
    nf-austin/valis -- whole slide image registration with VALIS

    Usage, samplesheet (recommended; this is what Seqera Platform launches with):
      nextflow run main.nf -profile docker --input samplesheet.csv --outdir results

      samplesheet.csv columns: set (optional), image
      Rows sharing a `set` are registered together, in row order; the FIRST row
      of each set is the reference. Sets run in parallel.

    Usage, ad-hoc single set:
      nextflow run main.nf -profile docker \\
          --images "reference.svs,round1.ome.tiff,round2.ome.tiff"

    Required (one of):
      --input         Samplesheet CSV.
      --images        Comma-separated, ordered list of 2 or more image paths.

    Common options:
      --name          Set name when using --images (default: reference basename).
      --outdir        Output directory (default: ${params.outdir}).
      --use_gpu       Use the CUDA image and a GPU-capable non-rigid registrar
                      (default: ${params.use_gpu}).
      --micro_reg     Second, higher-resolution non-rigid pass (default: ${params.micro_reg}).
      --merge         Also write a merged multi-channel OME-TIFF (default: ${params.merge}).
    """.stripIndent()
}

/**
 * Group samplesheet rows into registration sets.
 *
 * Done in plain Groovy over the fully-collected row list rather than with
 * groupTuple(), because the reference is defined positionally -- it is the first
 * row of each set -- and groupTuple() gives no ordering guarantee.
 */
def buildSets(rows, fallback_name, sheet_dir) {
    if (!rows) {
        error "Samplesheet is empty: ${params.input}"
    }
    if (!rows[0].containsKey('image')) {
        error "Samplesheet needs an 'image' column. Found: ${rows[0].keySet().join(', ')}"
    }

    def grouped = new LinkedHashMap()
    rows.eachWithIndex { row, idx ->
        def path = row.image?.trim()
        if (!path) {
            error "Samplesheet row ${idx + 1} has an empty 'image' value"
        }
        def key = row.set?.trim() ?: fallback_name
        grouped.computeIfAbsent(key, { _k -> [] }).add(resolveInput(path, sheet_dir, idx + 1))
    }

    return grouped.collect { set_id, files ->
        if (files.size() < 2) {
            error "Set '${set_id}' has ${files.size()} image(s); registration needs at least 2"
        }
        tuple(sanitize(set_id), files.collect { f -> f.name }, files)
    }
}

/**
 * Resolve one samplesheet entry to a file.
 *
 * A relative entry is resolved against the samplesheet's OWN directory first,
 * which is what someone editing that sheet expects. Nextflow's default is the
 * launch directory, and on Seqera Platform the launch directory is the work
 * directory -- so a relative path there silently resolves somewhere unrelated.
 * Falls back to launch-dir resolution so sheets that work today keep working,
 * and only then reports the entry as missing.
 */
def resolveInput(path, sheet_dir, row_num) {
    // Absolute POSIX path, or a remote URI (s3://, gs://, az://): take as-is.
    if (path.startsWith('/') || path ==~ /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/.*/) {
        return file(path, checkIfExists: true)
    }
    def beside_sheet = sheet_dir.resolve(path)
    if (beside_sheet.exists()) {
        return beside_sheet
    }
    def from_launch = file(path)
    if (from_launch.exists()) {
        return from_launch
    }
    error "Samplesheet row ${row_num}: image not found as '${beside_sheet}' (relative to the samplesheet) nor as '${from_launch}' (relative to the launch directory). Use an absolute path."
}

/** Output paths derive from this, so keep it filesystem-safe. */
def sanitize(name) {
    // simpleName strips only the final extension, so "ref.ome.tiff" would
    // otherwise leave "ref.ome" in every output path and filename.
    return name.toString()
        .replaceAll(/\.ome$/, '')
        .replaceAll(/[^A-Za-z0-9._-]+/, '_')
}

workflow {
    if (params.help) {
        helpMessage()
        return
    }

    if (params.input && params.images) {
        error "Use either --input (samplesheet) or --images (ad-hoc list), not both."
    }
    if (!params.input && !params.images) {
        error "No input given. Provide --input samplesheet.csv or --images \"first.svs,second.ome.tiff\". Run with --help for details."
    }

    if (params.input) {
        // Read and validate synchronously rather than through the splitCsv
        // channel operator: errors raised inside a channel closure are lazy --
        // they never fire under -preview, and in a real run they surface only
        // once the channel is consumed. A bad samplesheet should fail on launch.
        def sheet = file(params.input, checkIfExists: true)
        def rows = sheet.splitCsv(header: true, strip: true)
        ch_input = channel.fromList(buildSets(rows, params.name ?: 'registration', sheet.parent))
    }
    else {
        // Ad-hoc mode: one set, order taken from the comma-separated list.
        def image_paths = params.images.toString().split(',').collect { s -> s.trim() }.findAll { s -> s }
        if (image_paths.size() < 2) {
            error "Registration needs at least 2 images, got ${image_paths.size()}: ${image_paths}"
        }
        def image_files = image_paths.collect { p -> file(p, checkIfExists: true) }
        def set_id = sanitize(params.name ?: image_files[0].simpleName)
        ch_input = channel.of(tuple(set_id, image_files.collect { f -> f.name }, image_files))
    }

    log.info """
    P I P E L I N E   nf-austin/valis
    =================================
    input        : ${params.input ?: params.images}
    crop         : ${params.crop}
    reflections  : ${params.check_for_reflections}
    micro-reg    : ${params.micro_reg}
    gpu          : ${params.use_gpu}
    outdir       : ${params.outdir}
    """.stripIndent()

    RUN_VALIS(
        ch_input,
        channel.value(file("${projectDir}/modules/run_valis/run_valis.py"))
    )

    // One report per run, so a batch of sets summarises in a single document.
    SUMMARY_REPORT(
        RUN_VALIS.out.summary.map { _id, json -> json }.collect(),
        RUN_VALIS.out.qc.map { _id, dir -> dir }.collect(),
        channel.value(file("${projectDir}/modules/summary_report/summary_report.py"))
    )
}
