process RUN_VALIS {
    tag { set_id }
    publishDir { "${params.outdir}/${set_id}" }, mode: 'copy'

    // Pick the CPU or CUDA image unless the user pinned one explicitly.
    container { params.valis_container ?: (params.use_gpu ? params.valis_container_gpu : params.valis_container_cpu) }

    // Closure, not a plain string: workflow.containerEngine is not resolved at
    // config-parse time.
    containerOptions {
        params.use_gpu ? (workflow.containerEngine == 'singularity' ? '--nv' : '--gpus all') : null
    }

    input:
    // stageAs '?/*' puts each image in its own numbered directory, so inputs
    // that share a basename cannot collide. Order comes from `names`, which
    // run_valis.py asserts against the staged files.
    tuple val(set_id), val(names), path(images, stageAs: '?/*')
    path run_script

    output:
    tuple val(set_id), path("registered/*"),                            emit: registered
    tuple val(set_id), path("${set_id}_registration_error.csv"),        emit: error_csv
    tuple val(set_id), path("overlaps"),                                emit: qc,        optional: true
    tuple val(set_id), path("${set_id}_merged.ome.tiff"),               emit: merged,    optional: true
    tuple val(set_id), path("${set_id}_registrar.pickle"),              emit: registrar, optional: true

    script:
    def img_args  = (images instanceof List ? images : [images]).collect { f -> "'${f}'" }.join(' ')
    def name_args = (names  instanceof List ? names  : [names] ).collect { n -> "'${n}'" }.join(' ')
    def compression_arg = params.valis_compression ? "--compression '${params.valis_compression}'" : ''
    // VALIS resolves the device at import time, so CPU has to be forced through
    // the environment rather than a constructor argument.
    def gpu_env = params.use_gpu ? '' : 'export CUDA_VISIBLE_DEVICES=""'
    """
    set -euo pipefail

    # VALIS exposes no thread parameter -- pqdm is hardcoded to all-cores-minus-one
    # and nothing sets the BLAS or libvips limits. Left alone, those multiply on a
    # many-core node and hit the OpenBLAS thread ceiling (upstream issue #188).
    export OMP_NUM_THREADS=${task.cpus}
    export OPENBLAS_NUM_THREADS=${task.cpus}
    export MKL_NUM_THREADS=${task.cpus}
    export NUMEXPR_NUM_THREADS=${task.cpus}
    export VIPS_CONCURRENCY=${task.cpus}
    ${gpu_env}

    python3 ${run_script} \\
        --images ${img_args} \\
        --names ${name_args} \\
        --set-id '${set_id}' \\
        --outdir valis_out \\
        --registered-dir registered \\
        --crop '${params.crop}' \\
        --check-for-reflections ${params.check_for_reflections} \\
        --imgs-ordered ${params.imgs_ordered} \\
        --create-masks ${params.create_masks} \\
        --micro-reg ${params.micro_reg} \\
        --merge ${params.merge} \\
        --non-rigid-registrar '${params.use_gpu ? params.non_rigid_registrar_gpu : params.non_rigid_registrar}' \\
        --max-processed-image-dim-px ${params.max_processed_image_dim_px} \\
        --max-non-rigid-registration-dim-px ${params.max_non_rigid_registration_dim_px} \\
        --micro-max-dim-px ${params.micro_max_dim_px} \\
        ${compression_arg}
    """
}
