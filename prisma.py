"""Convert raw prose to Scenario and build a small Ren'Py project."""

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


CHAPTER = re.compile(r"#\s+(.+)")
RAW_SCENE = re.compile(r"[ \t\u3000]{2,}[0-9０-９]+[ \t\u3000]*")
SCENE_MARKERS = {"---", "◇◇◇"}
VIEWPOINT = re.compile(r"\$\[[^\[\]]+\]")
NAME = r"[\w·・？-]+"
DIALOGUE = re.compile(rf"(?P<name>{NAME})(?:\s*(?:（[^（）]*）|\([^()]*\)))?\s*[：:]\s*(?P<text>\S.*)")
RAW_QUOTE = re.compile(r"「[^「」]*(?:「[^「」]*」[^「」]*)*」")
UNKNOWN = "？"
LINE_MARKER = "<!-- prisma:line"
LINE_ID = re.compile(r"<!-- prisma:line ([a-zA-Z0-9][a-zA-Z0-9_-]{0,63}) -->")
LINE_CUES = "by_line_id"


@dataclass
class Line:
    text: str
    source: int
    speaker: str | None = None
    id: str | None = None


@dataclass
class Scene:
    chapter: str
    id: str
    lines: list[Line] = field(default_factory=list)


def draft_scenario(raw: str, speakers: dict, from_heading: str | None) -> tuple[str, list[int]]:
    """Use raw line numbers for speaker assignments, including after skipping a preface."""
    paragraphs = []
    pending = []
    used = set()
    active = from_heading is None
    scene_has_text = False
    quote = []

    def add_paragraph(number: int, line: str) -> None:
        nonlocal scene_has_text
        if RAW_QUOTE.fullmatch(line) and line[1:-1].strip():
            speaker = speakers.get(str(number), UNKNOWN)
            used.add(str(number))
            if speaker is not None:
                if not isinstance(speaker, str) or not re.fullmatch(NAME, speaker):
                    raise ValueError(f"raw line {number}: invalid speaker name")
                if speaker == UNKNOWN:
                    pending.append(number)
                line = f"{speaker}：{line[1:-1]}"
        elif line.startswith(("\\", LINE_MARKER)) or DIALOGUE.fullmatch(line):
            line = "\\" + line
        paragraphs.append(line.replace("\n", "\n  "))
        scene_has_text = True

    for number, raw_line in enumerate(raw.splitlines(), 1):
        line = raw_line.strip()
        if not active:
            if line != from_heading:
                continue
            active = True

        scene_break = line in SCENE_MARKERS or RAW_SCENE.fullmatch(raw_line) is not None
        if quote:
            if line and not line.startswith("「") and not CHAPTER.fullmatch(line) and not scene_break:
                quote.append((number, line))
                combined = "\n".join(part for _, part in quote)
                if combined.count("「") > combined.count("」"):
                    continue
                add_paragraph(quote[0][0], combined)
                quote.clear()
                continue
            for start, part in quote:
                add_paragraph(start, part)
            quote.clear()

        if not line:
            continue
        if CHAPTER.fullmatch(line):
            paragraphs.append(line)
            scene_has_text = False
        elif scene_break:
            if scene_has_text:
                paragraphs.append("---")
                scene_has_text = False
        elif VIEWPOINT.fullmatch(line):
            paragraphs.append(line)
        elif line.startswith("「") and line.count("「") > line.count("」"):
            quote.append((number, line))
        else:
            add_paragraph(number, line)

    for number, part in quote:
        add_paragraph(number, part)
    if not active:
        raise ValueError(f"raw heading not found: {from_heading}")
    if unused := speakers.keys() - used:
        raise ValueError(f"speaker assignments do not match quoted dialogue on raw lines: {', '.join(sorted(unused))}")
    if paragraphs and paragraphs[-1] == "---":
        paragraphs.pop()
    if not paragraphs:
        raise ValueError("raw text is empty")
    return "\n\n".join(paragraphs) + "\n", pending


def parse_scenario(source: str, *, allow_unresolved: bool = False) -> list[Scene]:
    scenes = []
    chapter = 1
    scene = Scene("正文", "1:1")
    scene_number = 1
    heading_seen = False
    continuation = None
    pending_id = None
    line_ids = set()

    for number, raw_line in enumerate(source.splitlines(), 1):
        line = raw_line.strip()
        if pending_id and (
            not line or line.startswith(LINE_MARKER) or line == "---"
            or CHAPTER.fullmatch(line) or VIEWPOINT.fullmatch(line)
        ):
            raise ValueError(f"Scenario line {number - 1}: line ID must immediately precede a text item")
        if not line:
            continuation = None
            continue
        if continuation is not None and raw_line.startswith("  "):
            continuation.text += "\n" + line
            continue
        continuation = None
        if line.startswith(LINE_MARKER):
            match = LINE_ID.fullmatch(line)
            if not match:
                raise ValueError(f"Scenario line {number}: malformed line ID")
            pending_id = match[1]
            if pending_id in line_ids:
                raise ValueError(f"Scenario line {number}: duplicate line ID {pending_id}")
            line_ids.add(pending_id)
        elif match := CHAPTER.fullmatch(line):
            if scene.lines:
                scenes.append(scene)
            if heading_seen or scene.lines:
                chapter += 1
            heading_seen = True
            scene_number = 1
            scene = Scene(match[1], f"{chapter}:1")
        elif line == "---":
            if not scene.lines:
                raise ValueError(f"Scenario line {number}: empty scene before separator")
            scenes.append(scene)
            scene_number += 1
            scene = Scene(scene.chapter, f"{chapter}:{scene_number}")
        elif VIEWPOINT.fullmatch(line):
            continue
        elif match := DIALOGUE.fullmatch(line):
            if match["name"] == UNKNOWN and not allow_unresolved:
                raise ValueError(f"Scenario line {number}: fill in the speaker name")
            continuation = Line(match["text"], number, match["name"], pending_id)
        else:
            if line.startswith("\\"):
                escaped = line[1:]
                if escaped.startswith(("\\", LINE_MARKER)) or escaped == "---" or any(
                    rule.fullmatch(escaped) for rule in (CHAPTER, VIEWPOINT, DIALOGUE)
                ):
                    line = escaped
            continuation = Line(line, number, id=pending_id)
        if continuation is not None:
            scene.lines.append(continuation)
            pending_id = None

    if pending_id:
        raise ValueError(f"Scenario line {number}: line ID must immediately precede a text item")
    if scene.lines:
        scenes.append(scene)
    if not scenes:
        raise ValueError("Scenario contains no playable text")
    return scenes


def anchor_scenario(source: str, cues: dict) -> tuple[str, dict]:
    """Persist text item IDs in Markdown and convert existing cues to those IDs."""
    scenes = parse_scenario(source, allow_unresolved=True)
    positions = resolve_cues(scenes, cues)
    checksum = hashlib.sha256(source.encode()).hexdigest()[:16]
    newline = re.search(r"\r\n|\r|\n", source)
    separator = newline[0] if newline else "\n"
    prefixes = {}
    bindings = {}
    for scene in scenes:
        for index, line in enumerate(scene.lines, 1):
            identifier = line.id
            if identifier is None:
                identifier = f"line-{checksum}-{line.source}"
                prefixes[line.source] = f"{LINE_MARKER} {identifier} -->{separator}"
            if str(index) in positions[scene.id]:
                commands = positions[scene.id][str(index)]
                if not isinstance(commands, list):
                    raise ValueError(f"scene {scene.id}, item {index}: expected a cue list")
                bindings[identifier] = commands
    source = "".join(prefixes.get(i, "") + row for i, row in enumerate(source.splitlines(keepends=True), 1))
    return source, {LINE_CUES: bindings} if bindings else {}


def quoted(value: str, *, dialogue: bool = False) -> str:
    if dialogue:
        escapes = {char: char * 2 for char in "[{〖%"}
        escapes.update({"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\u0009"})
        text = value.translate(str.maketrans(escapes))
        # Ren'Py's dialogue lexer collapses runs of unescaped spaces.
        text = re.sub(r" {2,}", lambda match: "\\ " * len(match[0]), text)
        return '"' + text + '"'
    return json.dumps(value, ensure_ascii=False)


def decode_mapping(text: str, label: str) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label}: duplicate key {key!r}")
            result[key] = value
        return result

    data = json.loads(text, object_pairs_hook=unique_keys)
    if not isinstance(data, dict):
        raise ValueError(f"{label}: expected a JSON object")
    return data


def read_mapping(path: Path) -> dict:
    return decode_mapping(path.read_text(encoding="utf-8-sig"), str(path))


def resolve_cues(scenes: list[Scene], cues: dict) -> dict:
    if unknown := cues.keys() - {LINE_CUES, *(scene.id for scene in scenes)}:
        raise ValueError(f"unknown scene in cues: {', '.join(sorted(unknown))}")
    positions = {}
    identifiers = {}
    for scene in scenes:
        scene_cues = cues.get(scene.id, {})
        if not isinstance(scene_cues, dict):
            raise ValueError(f"scene {scene.id}: expected an object of cue lists")
        if unknown := scene_cues.keys() - {str(index) for index in range(1, len(scene.lines) + 1)}:
            raise ValueError(f"scene {scene.id}: unknown text item in cues: {', '.join(sorted(unknown))}")
        positions[scene.id] = scene_cues.copy()
        for index, line in enumerate(scene.lines, 1):
            if line.id is not None:
                identifiers[line.id] = (scene.id, str(index))

    anchored = cues.get(LINE_CUES, {})
    if not isinstance(anchored, dict):
        raise ValueError(f"{LINE_CUES}: expected an object of cue lists")
    if unknown := anchored.keys() - identifiers.keys():
        raise ValueError(f"unknown line ID in cues: {', '.join(sorted(unknown))}")
    for identifier, commands in anchored.items():
        scene, index = identifiers[identifier]
        if index in positions[scene]:
            raise ValueError(f"scene {scene}, item {index}: cues use both a position and line ID {identifier}")
        positions[scene][index] = commands
    return positions


def render_script(
    scenes: list[Scene], cues: dict, assets_dir: Path, font: Path | None, *, confine_assets: bool = False
) -> tuple[str, dict]:
    """Render native Ren'Py statements and collect the files they reference."""
    images = {".png", ".jpg", ".jpeg", ".webp"}
    audio = {".ogg", ".opus", ".mp3", ".wav", ".flac"}
    files = {}
    positions = resolve_cues(scenes, cues)

    def add_asset(source: Path, extensions: set[str], context: str) -> str:
        suffix = source.suffix.lower()
        if suffix not in extensions or not source.is_file():
            raise ValueError(f"{context}: missing or unsupported asset {source}")
        with source.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        destination = f"prisma_assets/{checksum[:24]}{suffix}"
        files[destination] = source
        return quoted(destination)

    def asset(value, extensions, context):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{context}: expected a file path")
        source = assets_dir / value
        if confine_assets and not source.resolve().is_relative_to(assets_dir.resolve()):
            raise ValueError(f"{context}: select an uploaded asset")
        return add_asset(source, extensions, context)

    sprites = {}
    visible = set()
    speakers = {}
    body = ["label start:", f"    jump scene_{scenes[0].id.replace(':', '_')}", ""]
    headers = [
        "# Generated by Prisma. This is a native Ren'Py script.",
        'define config.name = "Prisma"',
        "define config.save_directory = None",
        "define config.screen_width = 1280",
        "define config.screen_height = 720",
        "",
        "transform prisma_backdrop:",
        "    xysize (1280, 720)",
        '    fit "cover"',
        "    align (0.5, 0.5)",
        "",
        "transform prisma_portrait:",
        "    ysize 720",
        '    fit "contain"',
        "    align (0.5, 1.0)",
    ]
    if font is not None:
        font_path = add_asset(font, {".ttf", ".otf", ".ttc"}, "font")
        headers.extend(("style default:", f"    font {font_path}"))
        for style in ("say_dialogue", "say_label"):
            headers.extend((f"style {style}:", f"    font {font_path}"))

    for scene_index, scene in enumerate(scenes):
        label = scene.id.replace(":", "_")
        body.extend((f"# {scene.id} {scene.chapter}", f"label scene_{label}:"))
        scene_cues = positions[scene.id]

        for index, line in enumerate(scene.lines, 1):
            body.append(f"    # {scene.id}/{index} (Scenario line {line.source})")
            commands = scene_cues.get(str(index), [])
            context = f"scene {scene.id}, item {index} (Scenario line {line.source})"
            if not isinstance(commands, list):
                raise ValueError(f"{context}: expected a cue list")
            voice_assigned = False

            for command in commands:
                if not isinstance(command, dict) or len(command) != 1:
                    raise ValueError(f"{context}: each cue must contain one action")
                action, value = next(iter(command.items()))
                if action == "background":
                    statement = (
                        f"show expression {asset(value, images, context)}"
                        " as prisma_background at prisma_backdrop zorder 0"
                    )
                elif action == "music":
                    statement = "stop music" if value is None else f"play music {asset(value, audio, context)}"
                elif action == "sound":
                    statement = f"play sound {asset(value, audio, context)}"
                elif action == "voice":
                    if line.speaker is None or voice_assigned:
                        raise ValueError(f"{context}: voice requires one spoken line and one file")
                    voice_assigned = True
                    statement = f"voice {asset(value, audio, context)}"
                elif action == "show":
                    if not isinstance(value, dict) or set(value) != {"actor", "file"}:
                        raise ValueError(f"{context}: show requires actor and file")
                    actor = value["actor"]
                    if not isinstance(actor, str) or not actor.strip():
                        raise ValueError(f"{context}: invalid actor")
                    tag = sprites.setdefault(actor, f"sprite_{len(sprites) + 1}")
                    visible.add(actor)
                    statement = f"show expression {asset(value['file'], images, context)} as {tag} at prisma_portrait zorder 10"
                elif action == "hide":
                    if not isinstance(value, str) or value not in visible:
                        raise ValueError(f"{context}: hide references an actor who is not visible")
                    visible.remove(value)
                    statement = f"hide {sprites[value]}"
                else:
                    raise ValueError(f"{context}: unsupported action {action!r}")
                body.append(f"    {statement}")

            content = quoted(line.text, dialogue=True)
            if line.speaker is not None:
                name = speakers.setdefault(line.speaker, f"speaker_{len(speakers) + 1}")
                content = f"{name} {content}"
            body.append(f"    {content}")

        if scene_index + 1 < len(scenes):
            following = scenes[scene_index + 1].id.replace(":", "_")
            body.append(f"    jump scene_{following}")
        else:
            body.append("    return")
        body.append("")

    headers.append("")
    for speaker, identifier in speakers.items():
        headers.append(f"define {identifier} = Character({quoted(speaker)})")
    return "\n".join(headers + [""] + body), files


def build_project(output: Path, script: str, files: dict) -> None:
    if output.exists():
        raise ValueError(f"output already exists: {output}. Choose a new directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prisma-", dir=output.parent) as temp:
        project = Path(temp) / "project"
        game = project / "game"
        game.mkdir(parents=True)
        (game / "script.rpy").write_text(script, encoding="utf-8", newline="\n")
        for destination, source in files.items():
            target = game / destination
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
        project.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="Prisma workspace for cataloging source and outputs")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft", help="prepare an editable Scenario from raw text")
    draft.add_argument("raw", type=Path)
    draft.add_argument("output", type=Path)
    draft.add_argument("--speakers", type=Path, help="JSON mapping raw line numbers to speaker names")
    draft.add_argument("--from-heading", help="start at this exact heading, including '# '")
    draft.add_argument("--line-ids", action="store_true", help="include stable text item IDs for cue bindings")
    build = commands.add_parser("build", help="turn a reviewed Scenario into a Ren'Py project")
    build.add_argument("scenario", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--cues", type=Path, help="JSON stage directions and selected files")
    build.add_argument("--font", type=Path, help="font file covering the script's language")
    args = parser.parse_args()

    try:
        from image_toolkit import ImageToolkit
        from prisma_workflow import StudioWorkflow

        toolkit = ImageToolkit(Path(__file__).resolve().parent, workspace=args.workspace)
        if args.command == "draft":
            speakers = read_mapping(args.speakers) if args.speakers else {}
            result = StudioWorkflow(toolkit.resources).draft(dict(raw=args.raw.read_text(encoding="utf-8-sig"),
                speakers=speakers, heading=args.from_heading or "", line_ids=args.line_ids))
            scenario, pending = result["scenario"], result["pending"]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(scenario)
            print(f"Scenario: {args.output}")
            if pending:
                preview = ", ".join(map(str, pending[:20]))
                if len(pending) > 20:
                    preview += ", ..."
                print(f"Speaker review needed for {len(pending)} quotes (raw lines: {preview})")
        else:
            from secrets import token_hex

            text = args.scenario.read_text(encoding="utf-8-sig")
            scenes = parse_scenario(text)
            cues = read_mapping(args.cues) if args.cues else {}
            assets_dir = args.cues.parent if args.cues else args.scenario.parent
            script, files = render_script(scenes, cues, assets_dir, args.font)
            build_project(args.output, script, files)
            _, links = toolkit.resources.snapshot(dict(scenario=text, cues=cues))
            for file in dict.fromkeys(files.values()):
                links.append(("uses_asset", "asset", toolkit.resources.record_local_asset(file)))
            toolkit.record_export(token_hex(8), args.output.resolve(), "renpy", links=links)
            print(f"Script: {args.output / 'game' / 'script.rpy'}")
    except (OSError, ValueError) as error:
        parser.exit(2, f"prisma: {error}\n")


if __name__ == "__main__":
    main()
