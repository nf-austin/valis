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

    output:
    tuple val(set_id), path("registered/*"),                            emit: registered
    tuple val(set_id), path("${set_id}_registration_error.csv"),        emit: error_csv
    // The PNGs, not the directory: every set's QC dir is named "overlaps", so
    // collecting directories for the report would collide. VALIS already
    // prefixes each file with the set name, and publishDir keeps the prefix.
    tuple val(set_id), path("overlaps/*"),                              emit: qc,        optional: true
    tuple val(set_id), path("${set_id}_summary.json"),                  emit: summary
    tuple val(set_id), path("${set_id}_merged.ome.tiff"),               emit: merged,    optional: true
    tuple val(set_id), path("${set_id}_registrar.pickle"),              emit: registrar, optional: true

    script:
    // Scripts live in bin/ and are called bare: Nextflow prepends
    // $projectDir/bin to PATH and bind-mounts it into the container, so this
    // works under docker, singularity and conda alike.
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

    run_valis.py \\
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

    // Lets `nextflow run ... -stub-run` exercise channel wiring, fan-out and the
    // summary report without pulling the multi-GB image or running registration.
    stub:
    def stub_names = (names instanceof List ? names : [names])
    def stub_json = groovy.json.JsonOutput.toJson([
        set_id: set_id,
        reference: stub_names[0],
        images: stub_names,
        n_images: stub_names.size(),
        reflections: [:],
        merged: true,
        settings: [
            crop: params.crop,
            check_for_reflections: params.check_for_reflections,
            create_masks: params.create_masks,
            micro_reg: params.micro_reg,
            non_rigid_registrar: params.non_rigid_registrar,
            max_processed_image_dim_px: params.max_processed_image_dim_px,
            max_non_rigid_registration_dim_px: params.max_non_rigid_registration_dim_px,
            micro_max_dim_px: params.micro_max_dim_px,
        ],
        metrics: [],
    ])
    """
    mkdir -p registered overlaps
    for n in ${stub_names.collect { n -> "'${n}'" }.join(' ')}; do
        touch "registered/\${n%%.*}.ome.tiff"
    done
    for stage in original_overlap rigid_overlap non_rigid_overlap micro_reg; do
        touch "overlaps/${set_id}_\${stage}.png"
    done
    echo 'filename,from,to,original_D,rigid_D,non_rigid_D' > ${set_id}_registration_error.csv
    touch ${set_id}_merged.ome.tiff ${set_id}_registrar.pickle
    echo '${stub_json}' > ${set_id}_summary.json
    """
}
