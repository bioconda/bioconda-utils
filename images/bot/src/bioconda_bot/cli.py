from logging import INFO, basicConfig

from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from asyncio import run
from typing import List, Optional


def build_parser_comment(parser: ArgumentParser) -> None:
    def run_command() -> None:
        from .comment import main as main_

        run(main_())

    parser.set_defaults(run_command=run_command)


def build_parser_merge(parser: ArgumentParser) -> None:
    def run_command() -> None:
        from .merge import main as main_

        run(main_())

    parser.set_defaults(run_command=run_command)


def build_parser_update(parser: ArgumentParser) -> None:
    def run_command() -> None:
        from .update import main as main_

        run(main_())

    parser.set_defaults(run_command=run_command)


def build_parser_automerge(parser: ArgumentParser) -> None:
    def run_command() -> None:
        from .automerge import main as main_

        run(main_())

    parser.set_defaults(run_command=run_command)


def build_parser_changeVisibility(parser: ArgumentParser) -> None:
    def run_command() -> None:
        from .changeVisibility import main as main_

        run(main_())

    parser.set_defaults(run_command=run_command)


def get_argument_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="bioconda-bot",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )
    sub_parsers = parser.add_subparsers(
        dest="command",
        required=True,
    )
    for command_name, build_parser in (
        ("comment", build_parser_comment),
        ("merge", build_parser_merge),
        ("update", build_parser_update),
        ("automerge", build_parser_automerge),
        ("change", build_parser_changeVisibility),
    ):
        sub_parser = sub_parsers.add_parser(
            command_name,
            formatter_class=ArgumentDefaultsHelpFormatter,
        )
        build_parser(sub_parser)
    return parser


def main(args: Optional[List[str]] = None) -> None:
    basicConfig(level=INFO)
    parser = get_argument_parser()
    parsed_args = parser.parse_args(args)
    parsed_args.run_command()
