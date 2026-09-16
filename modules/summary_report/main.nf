process SUMMARY_REPORT {
    // Spans every set, so no `tag`.
    publishDir { "${params.outdir}" }, mode: 'copy'

    // Reuses the VALIS image rather than pulling a second one: by this point it
    // is already cached on the node, and the script is pure standard library.
    container { params.valis_container ?: (params.use_gpu ? params.valis_container_gpu : params.valis_container_cpu) }

    input:
    path summaries, stageAs: 'summaries/*'
    path overlaps,  stageAs: 'overlaps/*'
    path run_script

    output:
    path "summary_report.html",   emit: report
    path "registration_qc.csv",   emit: metrics

    script:
    """
    python3 ${run_script} \\
        --summaries summaries \\
        --overlaps overlaps \\
        --run-name '${workflow.runName}' \\
        --revision '${workflow.manifest.version}' \\
        --out-html summary_report.html \\
        --out-csv registration_qc.csv
    """
}
