#!/usr/bin/env python3
"""Register a set of whole slide images with VALIS and warp them to the reference.

Everything lives in one script because ``register()``, ``register_micro()``,
``warp_and_save_slides()`` and ``warp_and_merge_slides()`` all operate on the
same in-memory ``Valis`` registrar -- splitting them across processes would mean
re-running registration.

The first image passed to ``--images`` is the reference; every other image is
warped into its coordinate frame at full resolution.
"""
import argparse
import inspect
import os
import shutil
import sys
import traceback
from pathlib import Path

# VALIS grabs CUDA at import time (DiskFD/LightGlueMatcher are instantiated at
# module scope), so CUDA_VISIBLE_DEVICES must already be set by the caller.
from valis import registration, non_rigid_registrars, valtils

# Kwargs this script depends on. Checked against the installed wheel up front so
# a VALIS upgrade that renames one fails loudly instead of silently ignoring it.
REQUIRED_KWARGS = (
    "reference_img_f",
    "align_to_reference",
    "check_for_reflections",
    "crop",
    "create_masks",
    "non_rigid_registrar_cls",
    "max_processed_image_dim_px",
    "max_non_rigid_registration_dim_px",
    "imgs_ordered",
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--images", nargs="+", required=True,
                   help="Staged image paths, in order. The first is the reference.")
    p.add_argument("--names", nargs="+", required=True,
                   help="Original basenames, in the same order as --images.")
    p.add_argument("--set-id", required=True, help="Name for this registration set.")
    p.add_argument("--outdir", default="valis_out", help="VALIS working/results directory.")
    p.add_argument("--registered-dir", default="registered",
                   help="Where the warped full-resolution slides are written.")

    p.add_argument("--crop", default="reference", choices=["reference", "overlap", "all"])
    p.add_argument("--check-for-reflections", default="true",
                   help="Search mirrored/flipped variants during rigid registration.")
    p.add_argument("--create-masks", default="true",
                   help="Let VALIS compute tissue/overlap masks. Disable if multi-Otsu "
                        "thresholding fails on low-contrast or near-uniform slides.")
    p.add_argument("--imgs-ordered", default="false",
                   help="Treat --images order as the z-stack order instead of sorting by similarity.")
    p.add_argument("--micro-reg", default="true", help="Run a second, higher-resolution non-rigid pass.")
    p.add_argument("--merge", default="true", help="Also write a merged multi-channel OME-TIFF.")

    p.add_argument("--non-rigid-registrar", default="OpticalFlowWarper",
                   help="Class name from valis.non_rigid_registrars.")
    p.add_argument("--max-processed-image-dim-px", type=int, default=512)
    p.add_argument("--max-non-rigid-registration-dim-px", type=int, default=2048)
    p.add_argument("--micro-max-dim-px", type=int, default=4096)
    p.add_argument("--compression", default=None, help="Override the OME-TIFF compression codec.")
    return p.parse_args(argv)


def as_bool(value):
    """Parse a Nextflow-supplied boolean, which arrives as the string true/false."""
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in ("true", "t", "yes", "y", "1"):
        return True
    if str(value).strip().lower() in ("false", "f", "no", "n", "0"):
        return False
    raise argparse.ArgumentTypeError(f"expected a boolean, got {value!r}")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def strip_prefix(name):
    """Undo the NN_ staging prefix so published names match the inputs."""
    base = os.path.basename(name)
    if len(base) > 3 and base[:2].isdigit() and base[2] == "_":
        base = base[3:]
    # VALIS names outputs "<stem>.ome.tiff", and valtils.get_name() strips only
    # the final extension -- so an "x.ome.tiff" input becomes "x.ome.ome.tiff".
    return base.replace(".ome.ome.tiff", ".ome.tiff")


def stage_slides(images, names, src_dir):
    """Link the ordered inputs into one flat directory with index prefixes.

    VALIS takes a source *directory* and sorts its contents itself, so the order
    the user typed has to be encoded into the filenames. Index prefixes do that
    and simultaneously make basenames unique -- two inputs can legitimately share
    one (``HE.svs`` from two different folders), and staging those side by side
    would otherwise collide.
    """
    if len(images) != len(names):
        fail(f"got {len(images)} images but {len(names)} names; these must correspond")
    if len(images) < 2:
        fail(f"registration needs at least 2 images, got {len(images)}")

    src_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for idx, (img, name) in enumerate(zip(images, names)):
        src = Path(img).resolve()
        if not src.exists():
            fail(f"staged image does not exist: {src}")
        # Guard the staging contract: Nextflow hands us paths in collection
        # order, but a mismatch here would silently pick the wrong reference.
        # Narrow gap: if two inputs share a basename, a swap between them passes
        # this check. Their content is what differs, which we cannot compare
        # cheaply, so accept it -- the ordering itself is still verified.
        if src.name != os.path.basename(name):
            fail(f"staging order mismatch at position {idx}: "
                 f"file is {src.name!r} but expected {os.path.basename(name)!r}")
        dst = src_dir / f"{idx:02d}_{src.name}"
        os.symlink(src, dst)
        staged.append(dst)

    stems = [valtils.get_name(str(p)) for p in staged]
    if len(set(stems)) != len(stems):
        fail(f"staged images do not have unique names: {stems}")
    return staged


def build_channel_name_dict(registrar):
    """Channel names for the merged image, with staging prefixes removed.

    Two jobs. Cosmetic: VALIS names each channel "{channel} ({slide name})" and
    our slide names carry the NN_ prefix. Load-bearing: RGB brightfield slides
    frequently report ``channel_names is None``, and VALIS's own default naming
    iterates that without a guard -- ``TypeError: 'NoneType' object is not
    iterable`` inside warp_and_merge_slides. Supplying a dict of our own keeps
    execution out of that branch entirely.

    Returns None only if the metadata is unreadable, in which case the caller
    skips merging rather than handing None to the broken default path.
    """
    try:
        mapping = {}
        for src_f in registrar.original_img_list:
            slide_obj = registrar.get_slide(src_f)
            clean = strip_prefix(slide_obj.name)
            meta = getattr(getattr(slide_obj, "reader", None), "metadata", None)
            channels = getattr(meta, "channel_names", None)
            n_channels = getattr(meta, "n_channels", None) or 1
            if not channels:
                channels = (["R", "G", "B"] if n_channels == 3
                            else [f"C{i + 1}" for i in range(n_channels)])
            mapping[src_f] = [f"{c} ({clean})" for c in channels]
        return mapping
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not read channel metadata ({exc}); "
              f"skipping the merged output", file=sys.stderr, flush=True)
        return None


def rename_outputs(registered_dir):
    """Strip NN_ prefixes from the warped slides so names match the inputs."""
    for path in sorted(registered_dir.iterdir()):
        if not path.is_file():
            continue
        clean = strip_prefix(path.name)
        if clean != path.name:
            target = path.parent / clean
            if target.exists():
                fail(f"cannot rename {path.name} to {clean}: target already exists")
            path.rename(target)


def main(argv=None):
    args = parse_args(argv)

    check_reflections = as_bool(args.check_for_reflections)
    imgs_ordered = as_bool(args.imgs_ordered)
    create_masks = as_bool(args.create_masks)
    micro_reg = as_bool(args.micro_reg)
    merge = as_bool(args.merge)

    missing = [k for k in REQUIRED_KWARGS
               if k not in inspect.signature(registration.Valis.__init__).parameters]
    if missing:
        fail(f"installed VALIS is missing expected arguments {missing}; "
             f"this pipeline was written against valis-wsi 1.2.0")

    if micro_reg and args.micro_max_dim_px <= args.max_non_rigid_registration_dim_px:
        fail(f"--micro-max-dim-px ({args.micro_max_dim_px}) must exceed "
             f"--max-non-rigid-registration-dim-px ({args.max_non_rigid_registration_dim_px}), "
             f"otherwise register_micro() silently does nothing")

    registrar_cls = getattr(non_rigid_registrars, args.non_rigid_registrar, None)
    if registrar_cls is None:
        available = [n for n in dir(non_rigid_registrars) if n.endswith(("Warper", "Registrar"))]
        fail(f"unknown non-rigid registrar {args.non_rigid_registrar!r}; available: {available}")

    src_dir = Path("slides").resolve()
    dst_dir = Path(args.outdir).resolve()
    registered_dir = Path(args.registered_dir).resolve()
    registered_dir.mkdir(parents=True, exist_ok=True)

    staged = stage_slides(args.images, args.names, src_dir)
    reference = staged[0]
    print(f"Reference image: {strip_prefix(reference.name)}", flush=True)
    print(f"Registering {len(staged)} images, reflections="
          f"{check_reflections}, micro={micro_reg}", flush=True)

    try:
        registrar = registration.Valis(
            src_dir=str(src_dir),
            dst_dir=str(dst_dir),
            name=args.set_id,
            # Both are required. reference_img_f alone leaves align_to_reference
            # False, which aligns images *serially toward* the reference rather
            # than directly to it -- the class docstring claims otherwise, but
            # the code never sets it.
            reference_img_f=str(reference),
            align_to_reference=True,
            # Off by default upstream; this is what handles mirrored/flipped
            # inputs. Rotation is already covered by the SimilarityTransform.
            check_for_reflections=check_reflections,
            crop=args.crop,
            imgs_ordered=imgs_ordered,
            # VALIS's mask step runs multi-Otsu over the combined tissue mask and
            # raises if it cannot find 4 classes -- which happens on uniform or
            # very low-contrast slides. This is the escape hatch.
            create_masks=create_masks,
            non_rigid_registrar_cls=registrar_cls(),
            max_processed_image_dim_px=args.max_processed_image_dim_px,
            max_non_rigid_registration_dim_px=args.max_non_rigid_registration_dim_px,
        )

        # register() traps every exception internally and returns (None, None,
        # None) rather than raising, so an unchecked call exits 0 having done
        # nothing.
        rigid_registrar, _non_rigid_registrar, error_df = registrar.register()
        if rigid_registrar is None or error_df is None:
            fail("VALIS registration failed; see the warnings above. "
                 "register() returned None rather than raising.")

        if micro_reg:
            print(f"Micro-registration at {args.micro_max_dim_px}px", flush=True)
            micro_registrar, micro_error_df = registrar.register_micro(
                max_non_rigid_registration_dim_px=args.micro_max_dim_px,
                reference_img_f=str(reference),
                align_to_reference=True,
            )
            if micro_registrar is None:
                fail("micro-registration failed or was skipped by VALIS")
            if micro_error_df is not None:
                error_df = micro_error_df

        error_csv = Path(f"{args.set_id}_registration_error.csv").resolve()
        error_df.to_csv(error_csv, index=False)
        print(f"Wrote {error_csv}", flush=True)

        warp_kwargs = {}
        if args.compression:
            warp_kwargs["compression"] = args.compression

        # level=0 is the full-resolution pyramid level.
        print(f"Warping slides at full resolution (crop={args.crop})", flush=True)
        registrar.warp_and_save_slides(
            str(registered_dir),
            level=0,
            crop=args.crop,
            pyramid=True,
            **warp_kwargs,
        )

        # Rename before merging: the warped slides are the primary deliverable
        # and must be correctly named even if the optional merge fails.
        rename_outputs(registered_dir)

        if merge:
            channel_names = build_channel_name_dict(registrar)
            if channel_names is None:
                print("WARNING: skipping merged output (no channel metadata)",
                      file=sys.stderr, flush=True)
            else:
                merged_f = Path(f"{args.set_id}_merged.ome.tiff").resolve()
                print(f"Merging into {merged_f.name}", flush=True)
                try:
                    registrar.warp_and_merge_slides(
                        str(merged_f),
                        level=0,
                        crop=args.crop,
                        channel_name_dict=channel_names,
                        drop_duplicates=True,
                        pyramid=True,
                        **warp_kwargs,
                    )
                except Exception as exc:  # noqa: BLE001
                    # The registered slides already exist and are the point of
                    # the run; losing them to a failed convenience output would
                    # be worse than shipping without it. Loud, not silent.
                    traceback.print_exc()
                    print(f"WARNING: merge failed ({exc}); registered slides are "
                          f"unaffected. Re-run with --merge false to skip it.",
                          file=sys.stderr, flush=True)

        # VALIS pickles the registrar itself during register(); surface it at a
        # predictable path so the process can publish it.
        if getattr(registrar, "reg_f", None) and os.path.exists(registrar.reg_f):
            shutil.copy2(registrar.reg_f, Path(f"{args.set_id}_registrar.pickle").resolve())

        # Lift the QC thumbnails out of VALIS's nested results tree so they
        # publish as a flat overlaps/ directory rather than valis_out/<name>/...
        overlaps_src = dst_dir / args.set_id / "overlaps"
        if overlaps_src.is_dir():
            shutil.copytree(overlaps_src, Path("overlaps").resolve(), dirs_exist_ok=True)

        print("VALIS registration complete", flush=True)

    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        fail("unhandled exception during registration")
    finally:
        # register() may already have killed the JVM on its failure path, so a
        # bare kill_jvm() here can raise and mask the original error.
        try:
            registration.kill_jvm()
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: kill_jvm() failed: {exc}", file=sys.stderr, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
