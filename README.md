# nf-austin/valis

A Nextflow DSL2 pipeline for automated whole slide image registration using
[VALIS](https://github.com/MathOnco/valis) (Virtual Alignment of pathoLogy Image Series). Takes two
or more slides, aligns them rigidly and non-rigidly, and writes **full-resolution warped OME-TIFFs
in the coordinate frame of the first image you provide**.

Handles slides that are rotated *or mirrored/flipped* relative to each other, needs no manual
landmarks, and runs on CPU by default with an optional GPU path.

## Pipeline steps

1. **RUN_VALIS** (`run_valis`) — the whole registration in one process, because every stage shares
   the in-memory VALIS registrar:
   - stages the ordered inputs so the first one is unambiguously the reference,
   - rigid registration, including a reflection search so mirrored/flipped inputs align,
   - non-rigid registration, plus an optional higher-resolution micro-registration pass,
   - full-resolution warping of every slide into the reference's frame,
   - an optional merged multi-channel OME-TIFF, a registration-error CSV, and QC overlap thumbnails.
2. **SUMMARY_REPORT** (`summary_report`) — aggregates every set into one self-contained
   `summary_report.html` (metrics table plus before/after overlays, embedded) and a flat
   `registration_qc.csv`. Runs once per pipeline run, after all sets finish.

## Requirements

- Nextflow >= 24.04.0
- Docker or Singularity

`-profile conda` is **not** supported: `valis-wsi` is PyPI-only behind a JDK/libvips native stack, so
`RUN_VALIS` resolves through a container image instead of an `environment.yml`.

## Usage

### Samplesheet (recommended, and what Seqera Platform launches with)

```bash
nextflow run nf-austin/valis \
    -profile docker \
    --input samplesheet.csv \
    --outdir results
```

`samplesheet.csv` has two columns, `set` (optional) and `image`:

```csv
set,image
case01,/data/case01/HE.svs
case01,/data/case01/IF_round1.ome.tiff
case01,/data/case01/IF_round2.ome.tiff
case02,/data/case02/HE.svs
case02,/data/case02/IF_round1.ome.tiff
```

Rows sharing a `set` are registered together **in row order**, and the **first row of each set is
the reference** every other image is warped onto. Sets run in parallel, each publishing to
`results/<set>/`. Omit the `set` column to treat the whole sheet as a single set. An example lives
at [`assets/samplesheet_example.csv`](assets/samplesheet_example.csv).

### Ad-hoc single set

```bash
nextflow run nf-austin/valis \
    -profile docker \
    --images "reference.svs,round1.ome.tiff,round2.ome.tiff"
```

The **first** entry is the reference. Convenient from a terminal; prefer `--input` on Seqera
Platform, where a samplesheet gets a file browser rather than a text box.

With a GPU:

```bash
nextflow run nf-austin/valis \
    -profile docker \
    --images "reference.svs,round1.ome.tiff" \
    --use_gpu true
```

Faster, more precise, or cheaper variants:

```bash
# Higher-resolution non-rigid registration (slower, more accurate)
--max_non_rigid_registration_dim_px 4096 --micro_max_dim_px 8192

# Skip micro-registration for a quicker run
--micro_reg false

# Inputs are known to share orientation -- skip the reflection search (~4x less
# rigid-stage work)
--check_for_reflections false
```

## Parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `--input` | *(one of these two)* | Samplesheet CSV with `set` (optional) and `image` columns. |
| `--images` | *(one of these two)* | Ad-hoc comma-separated, ordered list of 2+ image paths. The first is the reference. |
| `--name` | reference basename | Set name, used for the output subdirectory. Applies to `--images`, or as the fallback for a sheet with no `set` column. |
| `--outdir` | `results` | Output directory. |
| `--use_gpu` | `false` | Use the CUDA image, request an accelerator, and switch to a GPU-capable non-rigid registrar. |
| `--crop` | `reference` | `reference` (outputs match the reference's frame and dimensions), `overlap` (common region only), or `all` (union extent). |
| `--check_for_reflections` | `true` | Search mirrored/flipped variants during rigid registration. Off in VALIS itself. |
| `--imgs_ordered` | `false` | Treat the `--images` order as the z-stack order instead of letting VALIS sort by similarity. |
| `--create_masks` | `true` | Let VALIS compute tissue/overlap masks. Set `false` if its multi-Otsu thresholding fails on low-contrast or near-uniform slides. |
| `--micro_reg` | `true` | Run a second, higher-resolution non-rigid pass. |
| `--merge` | `true` | Also write a merged multi-channel OME-TIFF. |
| `--non_rigid_registrar` | `OpticalFlowWarper` | Class from `valis.non_rigid_registrars` used on CPU. |
| `--non_rigid_registrar_gpu` | `RAFTWarper` | Class used when `--use_gpu true`. The only GPU-capable registrar. |
| `--max_processed_image_dim_px` | `512` | Working size for feature detection. |
| `--max_non_rigid_registration_dim_px` | `2048` | Working size for non-rigid registration. |
| `--micro_max_dim_px` | `4096` | Working size for micro-registration. Must exceed the value above. |
| `--valis_compression` | *(VALIS default)* | OME-TIFF codec override; VALIS defaults to DEFLATE. |
| `--valis_container` | *(auto)* | Pin a specific image, overriding the CPU/GPU selection. |
| `--max_memory` | `128.GB` | Memory cap applied to all processes. |
| `--max_cpus` | `32` | CPU cap applied to all processes. |
| `--max_time` | `72.h` | Runtime cap applied to all processes. |

## Output structure

```text
results/
├── summary_report.html                  # Self-contained run report: per-set metrics
│                                        #   plus before/after overlays. Start here.
├── registration_qc.csv                  # Every slide's metrics, flat, all sets
├── <set>/
│   ├── registered/                      # Full-resolution warped slides, in the
│   │   ├── HE.ome.tiff                  #   reference's coordinate frame. Input
│   │   ├── IF_round1.ome.tiff           #   filenames are preserved.
│   │   └── IF_round2.ome.tiff
│   ├── overlaps/                        # Low-res QC thumbnails per stage
│   ├── <set>_registration_error.csv     # Raw VALIS error: original / rigid /
│   │                                    #   non-rigid D and rTRE
│   ├── <set>_summary.json               # Machine-readable set summary
│   ├── <set>_merged.ome.tiff            # Merged multi-channel stack (--merge)
│   └── <set>_registrar.pickle           # Pickled registrar; re-apply transforms
│                                        #   later without re-registering
└── pipeline_info/                       # Nextflow execution report, timeline,
                                         #   trace and DAG
```

## Seqera Platform (Nextflow Tower)

The repo ships everything Platform needs:

- **`nextflow_schema.json`** — renders the launch form. `--input` appears as a file picker wired to
  Data Explorer, registration options are grouped and documented, and tuning knobs are marked
  hidden so the default form stays short.
- **`assets/schema_input.json`** — the samplesheet contract (`set`, `image`), so a malformed sheet
  is caught before compute is provisioned.
- **`tower.yml`** — puts `summary_report.html`, `registration_qc.csv`, the per-set error CSVs and
  the Nextflow execution report in the run's **Reports** tab.

To add it: **Pipelines → Add pipeline**, point at this repository, and pick a compute environment.
Use absolute cloud paths (`s3://...`) for `--input`, the images it references, and `--outdir`.

For `--use_gpu true`, the compute environment must be able to allocate an NVIDIA GPU — the process
requests `accelerator 1` and runs the `-cuda` image, which is `linux/amd64` only.

## Container images

Built from `modules/run_valis/Dockerfile` and published to GHCR by
`.github/workflows/docker.yml` in two variants:

| Tag | Base | Platforms | Used when |
| --- | --- | --- | --- |
| `ghcr.io/nf-austin/valis:<ver>` | `python:3.11-slim` | amd64, arm64 | default (CPU) |
| `ghcr.io/nf-austin/valis:<ver>-cuda` | `pytorch/pytorch:*-cuda12.8-*` | amd64 | `--use_gpu true` |

Both bake in the Bio-Formats jar and the DISK/DeDoDe/LightGlue/RAFT model weights, so runs need no
network egress. Both also carry two upstream fixes, applied as mechanical `sed` patches with
build-time assertions that fail the build if they stop applying:

1. [PR #239](https://github.com/MathOnco/valis/pull/239) — tensors are sent to numpy without
   `.cpu()`, which breaks VALIS's CUDA path entirely
   ([issue #208](https://github.com/MathOnco/valis/issues/208)).
2. **A LightGlue dtype fix.** `LightGlueMatcher.match_images` builds tensors with
   `torch.from_numpy(...)` and no float cast, while the model weights are float32
   (`SuperGlueMatcher` right above it *does* cast). It bites whenever the incoming keypoints are
   float64 — which is exactly what the reflection search produces, since it derives flipped
   keypoints with numpy. Without this patch, `--check_for_reflections true` fails with
   `expected m1 and m2 to have the same dtype, but got: double != float`, and because `register()`
   swallows exceptions it surfaces only as a silent `None`.

## Notes

- **Formats with sidecar directories** (`.mrxs` and similar, where a companion folder sits next to
  the image) are not currently handled: inputs are staged individually, so the sidecar is not carried
  along. Convert to a single-file format first.
- **Memory.** VALIS switches to an experimental tiled non-rigid registrar when it estimates >10 GB
  of in-memory images. Lower `--max_non_rigid_registration_dim_px` if you hit this.
- **If registration fails**, VALIS most often ran out of usable feature matches. In order of
  usefulness: raise `--max_processed_image_dim_px` (512 → 1024), set `--create_masks false` if the
  error mentions multi-Otsu thresholding, and check `overlaps/*_rigid_overlap.png` — where the two
  images coincide they render grey/white, and where they do not you see separated magenta and green.
  A degenerate transform surfaces as `cannot convert float NaN to integer`.
