from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import DrainageConfig, DrainageError, DrainagePipeline, WhiteboxRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dem-drainage",
        description=(
            "Extrai uma rede de drenagem separadamente para cada arquivo DEM, calcula a ordem de Hack "
            "usando o WhiteboxTools."
        ),
    )
    parser.add_argument("dem_folder", type=Path, help="Pasta que contém os arquivos DEM em GeoTIFF.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Pasta de saída (padrão: <dem_folder>/drainage_output).",
    )
    threshold = parser.add_mutually_exclusive_group()
    threshold.add_argument(
        "--threshold-cells",
        type=float,
        default=None,
        metavar="N",
        help="Número mínimo de células acumuladas para iniciar um canal (padrão: 10000).",
    )
    threshold.add_argument(
        "--threshold-area",
        type=float,
        default=None,
        metavar="AREA",
        help="Área mínima de contribuição para iniciar um canal, nas unidades quadradas do SRC.",
    )
    parser.add_argument(
        "--no-fill-remaining",
        action="store_true",
        help="Não preenche depressões que não possam ser corrigidas dentro dos limites.",
    )
    parser.add_argument(
        "--min-stream-length",
        type=float,
        metavar="MAP_UNITS",
        help="Remove tributários menores que esse comprimento antes de calcular a ordem da rede.",
    )
    parser.add_argument("--recursive", action="store_true", help="Procura DEMs em subpastas.")
    parser.add_argument(
        "--pattern",
        action="append",
        dest="patterns",
        metavar="GLOB",
        help="Padrão glob dos arquivos de entrada; pode ser repetido (padrão: *.tif e *.tiff).",
    )
    parser.add_argument("--resume", action="store_true", help="Reutiliza saídas de etapas existentes.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Mantém somente o vetor final, o raster de ordem Hack e os metadados.",
    )
    parser.add_argument(
        "--whitebox-exe",
        type=Path,
        help="Caminho para o executável jblindsay/whitebox-tools.",
    )
    parser.add_argument(
        "--quiet-whitebox",
        action="store_true",
        help="Não exibe a saída detalhada do WhiteboxTools.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Exibe os comandos do WhiteboxTools sem executá-los.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_dir = args.dem_folder.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else input_dir / "drainage_output"
    )
    threshold = args.threshold_area
    units = "area"
    if threshold is None:
        threshold = args.threshold_cells if args.threshold_cells is not None else 10_000.0
        units = "cells"

    try:
        executable = WhiteboxRunner.discover(args.whitebox_exe)
        config = DrainageConfig(
            input_dir=input_dir,
            output_dir=output_dir,
            threshold=threshold,
            threshold_units=units,
            fill_remaining_depressions=not args.no_fill_remaining,
            min_stream_length=args.min_stream_length,
            recursive=args.recursive,
            patterns=tuple(args.patterns or ("*.tif", "*.tiff")),
            resume=args.resume,
            keep_intermediates=not args.clean,
        )
        result = DrainagePipeline(
            config,
            WhiteboxRunner(
                executable,
                verbose=not args.quiet_whitebox,
                dry_run=args.dry_run,
            ),
        ).execute()
    except DrainageError as exc:
        parser.exit(2, f"erro: {exc}\n")

    for tile in result:
        print(f"{tile.source_dem.name} -> {tile.drainage_geoparquet}")
    print("Hierarquia: ordem Hack 1 = drenagem principal; 2+ = níveis sucessivos de tributários")
    return 0


if __name__ == "__main__":
    sys.exit(main())
