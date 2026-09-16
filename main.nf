#!/usr/bin/env nextflow

nextflow.enable.dsl = 2

include { RUN_VALIS } from './modules/run_valis/main.nf'

def helpMessage() {
    log.info """
    nf-austin/valis -- whole slide image registration with VALIS

    Usage:
      nextflow run main.nf -profile docker \\
          --images "reference.svs,round1.ome.tiff,round2.ome.tiff"

    The FIRST image in --images is the reference; every other image is warped
    into its coordinate frame at full resolution.

    Required:
      --images        Comma-separated, ordered list of 2 or more image paths.

    Common options:
      --name          Name for this registration set (default: reference basename).
      --outdir        Output directory (default: ${params.outdir}).
      --use_gpu       Use the CUDA image and a GPU-capable non-rigid registrar
                      (default: ${params.use_gpu}).
      --micro_reg     Second, higher-resolution non-rigid pass (default: ${params.micro_reg}).
      --merge         Also write a merged multi-channel OME-TIFF (default: ${params.merge}).
    """.stripIndent()
}

workflow {
    if (params.help) {
        helpMessage()
        return
    }

    if (!params.images) {
        error "No images given. Use --images \"first.svs,second.ome.tiff\" (the first is the reference). Run with --help for details."
    }

    // Ordered list -- position matters, since images[0] is the reference.
    def image_paths = params.images
        .toString()
        .split(',')
        .collect { s -> s.trim() }
        .findAll { s -> s }

    if (image_paths.size() < 2) {
        error "Registration needs at least 2 images, got ${image_paths.size()}: ${image_paths}"
    }

    def image_files = image_paths.collect { p ->
        def f = file(p)
        if (!f.exists()) {
            error "Image not found: ${p}"
        }
        f
    }

    // simpleName strips only the last extension, so "ref.ome.tiff" would leave
    // "ref.ome" in every output path and filename.
    def set_id = (params.name ?: image_files[0].simpleName)
        .toString()
        .replaceAll(/\.ome$/, '')
        .replaceAll(/\s+/, '_')

    log.info """
    P I P E L I N E   nf-austin/valis
    =================================
    set          : ${set_id}
    images       : ${image_files.size()}
    reference    : ${image_files[0].name}
    crop         : ${params.crop}
    reflections  : ${params.check_for_reflections}
    micro-reg    : ${params.micro_reg}
    gpu          : ${params.use_gpu}
    outdir       : ${params.outdir}
    """.stripIndent()

    // One registration set per run, so this is a single-element channel rather
    // than the per-sample fan-out the sibling pipelines use. The basenames ride
    // along so the process can assert the staged order matches.
    ch_input = channel.of(
        tuple(set_id, image_files.collect { f -> f.name }, image_files)
    )

    RUN_VALIS(
        ch_input,
        channel.value(file("${projectDir}/modules/run_valis/run_valis.py"))
    )
}
