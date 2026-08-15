from __future__ import annotations

import argparse
from pathlib import Path

from data_pipeline.text.chunk_builder import build_chunks
from data_pipeline.text.chunker import ChunkingConfig
from data_pipeline.text.document_builder import build_documents


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the normalized FinTrace text document corpus.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    documents = subparsers.add_parser("build-documents", help="Build documents.jsonl from announcements and research reports")
    documents.add_argument("--data-dir", type=Path, default=Path("data"), help="Preprocessed data root")
    documents.add_argument("--output", type=Path, default=None, help="Output JSONL path")
    documents.add_argument("--report", type=Path, default=None, help="Quality report path")
    chunks = subparsers.add_parser("build-chunks", help="Build chunks.jsonl from normalized Documents")
    chunks.add_argument("--data-dir", type=Path, default=Path("data"), help="Preprocessed data root")
    chunks.add_argument("--documents", type=Path, default=None, help="Input documents.jsonl path")
    chunks.add_argument("--output", type=Path, default=None, help="Output chunks.jsonl path")
    chunks.add_argument("--report", type=Path, default=None, help="Chunk quality report path")
    chunks.add_argument("--manifest", type=Path, default=None, help="Chunk manifest path")
    chunks.add_argument("--version", default="chunks-v1", help="Frozen Chunk corpus version")
    chunks.add_argument("--target-chars", type=int, default=600)
    chunks.add_argument("--min-chars", type=int, default=200)
    chunks.add_argument("--soft-max-chars", type=int, default=900)
    chunks.add_argument("--hard-max-chars", type=int, default=1200)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build-documents":
        report = build_documents(data_dir=args.data_dir, output_path=args.output, report_path=args.report)
        announcements = report["datasets"]["announcement"]
        research = report["datasets"]["research_report"]
        print(
            "Built normalized text documents: "
            f"total={report['total_documents']}, "
            f"announcements={announcements['output']}, "
            f"research_reports={research['output']}, "
            f"output={report['output']}"
        )
        return 0
    if args.command == "build-chunks":
        config = ChunkingConfig(
            target_chars=args.target_chars,
            min_chars=args.min_chars,
            soft_max_chars=args.soft_max_chars,
            hard_max_chars=args.hard_max_chars,
        )
        report = build_chunks(
            data_dir=args.data_dir,
            documents_path=args.documents,
            output_path=args.output,
            report_path=args.report,
            manifest_path=args.manifest,
            version=args.version,
            config=config,
        )
        print(
            "Built normalized text chunks: "
            f"documents={report['total_documents']}, "
            f"chunks={report['total_chunks']}, "
            f"output={report['output']}"
        )
        return 0
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
