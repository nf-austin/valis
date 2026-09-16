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

    // Keeps -stub-run from executing the real script, which would need the
    // staged summaries to exist. Filenames must match the output: block above.
    stub:
    """
    echo '<html><body><h1>stub</h1></body></html>' > summary_report.html
    echo 'set_id,filename,original_D,rigid_D,non_rigid_D' > registration_qc.csv
    """
}
