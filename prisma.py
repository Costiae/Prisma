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
IDENTIFIER = r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}"
LINE_ID = re.compile(rf"<!-- prisma:line ({IDENTIFIER}) -->")
CHAPTER_ID = re.compile(r"[1-9][0-9]*")
LINE_CUES = "by_line_id"
SCENARIO_VERSION = 1


@dataclass
class Line:
    text: str
    source: int | None
    speaker: str | None = None
    id: str | None = None
    source_kind: str | None = None
    viewpoint: str | None = None
    direction: str | None = None


@dataclass
class Scene:
    chapter: str
    id: str
    lines: list[Line] = field(default_factory=list)


def draft_scenario(raw: str, speakers: dict, from_heading: str | None) -> tuple[dict, list[int]]:
    """Use raw line numbers for speaker assignments, including after skipping a preface."""
    scenes = []
    chapter = scene_number = 1
    scene = Scene("正文", "1:1")
    heading_seen = False
    pending = []
    used = set()
    active = from_heading is None
    quote = []
    viewpoint = None
    checksum = hashlib.sha256(raw.encode()).hexdigest()[:16]

    def add_paragraph(number: int, line: str) -> None:
        speaker = None
        if RAW_QUOTE.fullmatch(line) and line[1:-1].strip():
            speaker = speakers.get(str(number), UNKNOWN)
            used.add(str(number))
            if speaker is not None:
                if not isinstance(speaker, str) or not re.fullmatch(NAME, speaker):
                    raise ValueError(f"raw line {number}: invalid speaker name")
                if speaker == UNKNOWN:
                    pending.append(number)
                line = line[1:-1]
        scene.lines.append(Line(line, number, speaker, f"line-{checksum}-{number}",
                                source_kind="raw", viewpoint=viewpoint))

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
        if match := CHAPTER.fullmatch(line):
            if scene.lines:
                scenes.append(scene)
            if heading_seen or scene.lines or scene_number > 1:
                chapter += 1
            heading_seen = True
            scene_number = 1
            scene = Scene(match[1], f"{chapter}:1")
        elif scene_break:
            if scene.lines:
                scenes.append(scene)
                scene_number += 1
                scene = Scene(scene.chapter, f"{chapter}:{scene_number}")
        elif VIEWPOINT.fullmatch(line):
            viewpoint = line[2:-1]
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
    if scene.lines:
        scenes.append(scene)
    if not scenes:
        raise ValueError("raw text contains no playable text")
    return scenario_document(scenes), pending


def read_legacy_scenario(source: str) -> list[Scene]:
    scenes = []
    chapter = 1
    scene = Scene("正文", "1:1")
    scene_number = 1
    heading_seen = False
    continuation = None
    pending_id = None
    line_ids = set()
    viewpoint = None

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
            if heading_seen or scene.lines or scene_number > 1:
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
            viewpoint = line[2:-1]
        elif match := DIALOGUE.fullmatch(line):
            end_of_speaker = line[match.end("name"):match.start("text")].rstrip()
            direction = end_of_speaker[:-1].strip() if end_of_speaker else ""
            continuation = Line(match["text"], number, match["name"], pending_id,
                                direction=direction[1:-1] if direction else None)
        else:
            if line.startswith("\\"):
                escaped = line[1:]
                if escaped.startswith(("\\", LINE_MARKER)) or escaped == "---" or any(
                    rule.fullmatch(escaped) for rule in (CHAPTER, VIEWPOINT, DIALOGUE)
                ):
                    line = escaped
            continuation = Line(line, number, id=pending_id)
        if continuation is not None:
            continuation.source_kind = "legacy"
            continuation.viewpoint = viewpoint
            scene.lines.append(continuation)
            pending_id = None

    if pending_id:
        raise ValueError(f"Scenario line {number}: line ID must immediately precede a text item")
    if scene.lines:
        scenes.append(scene)
    if not scenes:
        raise ValueError("Scenario contains no playable text")
    return scenes


def read_json_scenario(document: dict) -> list[Scene]:
    if type(document.get("version")) is not int or document["version"] != SCENARIO_VERSION:
        raise ValueError("Scenario JSON version must be 1")
    chapters = document.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("Scenario JSON must contain chapters")
    scenes = []
    chapter_ids, scene_ids = set(), set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Scenario chapter must be an object")
        ident, title, chapters_scenes = chapter.get("id"), chapter.get("title"), chapter.get("scenes")
        if (not isinstance(ident, str) or not CHAPTER_ID.fullmatch(ident) or ident in chapter_ids
                or not isinstance(title, str) or not title.strip()
                or not isinstance(chapters_scenes, list) or not chapters_scenes):
            raise ValueError("Scenario chapter needs a unique numeric id, title and scenes")
        chapter_ids.add(ident)
        for item in chapters_scenes:
            if not isinstance(item, dict):
                raise ValueError("Scenario scene must be an object")
            scene_id, lines = item.get("id"), item.get("lines")
            if (not isinstance(scene_id, str) or not re.fullmatch(rf"{ident}:[1-9][0-9]*", scene_id)
                    or scene_id in scene_ids or not isinstance(lines, list) or not lines):
                raise ValueError(f"chapter {ident}: each scene needs a unique id and text items")
            scene_ids.add(scene_id)
            scene = Scene(title, scene_id)
            for index, entry in enumerate(lines, 1):
                context = f"scene {scene_id}, item {index}"
                if not isinstance(entry, dict):
                    raise ValueError(f"{context}: text item must be an object")
                text, speaker, line_id = entry.get("text"), entry.get("speaker"), entry.get("id")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(f"{context}: text is empty")
                if speaker is not None and (not isinstance(speaker, str) or not speaker.strip()):
                    raise ValueError(f"{context}: invalid speaker")
                if line_id is not None and (not isinstance(line_id, str) or not re.fullmatch(IDENTIFIER, line_id)):
                    raise ValueError(f"{context}: invalid line id")
                source = entry.get("source")
                if source is not None and (not isinstance(source, dict) or source.get("kind") not in ("raw", "legacy")
                        or type(source.get("line")) is not int or source["line"] < 1):
                    raise ValueError(f"{context}: invalid source reference")
                annotations = {key: entry.get(key) for key in ("viewpoint", "direction")}
                if any(value is not None and not isinstance(value, str) for value in annotations.values()):
                    raise ValueError(f"{context}: invalid viewpoint or direction")
                scene.lines.append(Line(text, source["line"] if source else None, speaker, line_id,
                                        source_kind=source["kind"] if source else None, **annotations))
            scenes.append(scene)
    return scenes


def parse_scenario(source, *, allow_unresolved: bool = False) -> list[Scene]:
    if isinstance(source, str):
        source = source.lstrip("\ufeff")
        if source.lstrip().startswith(("{", "[")):
            source = decode_mapping(source, "Scenario")
    if isinstance(source, dict):
        scenes = read_json_scenario(source)
        legacy = False
    elif isinstance(source, str):
        scenes = read_legacy_scenario(source)
        legacy = True
    else:
        raise ValueError("Scenario must be a JSON object or legacy text")
    ids = set()
    checksum = None
    for scene in scenes:
        for index, line in enumerate(scene.lines, 1):
            context = f"scene {scene.id}, item {index}"
            if line.id is None:
                if checksum is None:
                    value = source if legacy else json.dumps(source, sort_keys=True, ensure_ascii=False)
                    checksum = hashlib.sha256(value.encode()).hexdigest()[:16]
                suffix = line.source if legacy else f"{scene.id.replace(':', '-')}-{index}"
                line.id = f"line-{checksum}-{suffix}"
            if line.id in ids:
                raise ValueError(f"{context}: duplicate line ID {line.id}")
            ids.add(line.id)
            if line.speaker == UNKNOWN and not allow_unresolved:
                raise ValueError(f"{context}: fill in the speaker name")
    return scenes


def scenario_document(scenes: list[Scene]) -> dict:
    chapters = {}
    for scene in scenes:
        ident = scene.id.split(":", 1)[0]
        chapter = chapters.setdefault(ident, {"id": ident, "title": scene.chapter, "scenes": []})
        lines = []
        for line in scene.lines:
            item = {"id": line.id, "speaker": line.speaker, "text": line.text}
            if line.source is not None:
                item["source"] = {"kind": line.source_kind, "line": line.source}
            for key in ("viewpoint", "direction"):
                if value := getattr(line, key):
                    item[key] = value
            lines.append(item)
        chapter["scenes"].append({"id": scene.id, "lines": lines})
    return {"version": SCENARIO_VERSION, "chapters": list(chapters.values())}


def normalize_scenario(source) -> dict:
    return scenario_document(parse_scenario(source, allow_unresolved=True))


def anchor_scenario(source, cues: dict) -> tuple[dict, dict]:
    """Convert positional cues to stable IDs and return the canonical JSON Scenario."""
    scenes = parse_scenario(source, allow_unresolved=True)
    positions = resolve_cues(scenes, cues)
    bindings = {}
    for scene in scenes:
        for index, line in enumerate(scene.lines, 1):
            if str(index) in positions[scene.id]:
                commands = positions[scene.id][str(index)]
                if not isinstance(commands, list):
                    raise ValueError(f"scene {scene.id}, item {index}: expected a cue list")
                bindings[line.id] = commands
    return scenario_document(scenes), {LINE_CUES: bindings} if bindings else {}


def quoted(value: str, *, dialogue: bool = False) -> str:
    if dialogue:
        escapes = {char: char * 2 for char in "[{〖%"}
        escapes.update({"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\u0009"})
        text = value.translate(str.maketrans(escapes))
        # Ren'Py's dialogue lexer collapses runs of unescaped spaces.
        text = re.sub(r" {2,}", lambda match: "\\ " * len(match[0]), text)
        return '"' + text + '"'
    return json.dumps(value, ensure_ascii=False)


def project_resolution(config=None) -> tuple[int, int]:
    if config is None:
        return 1280, 720
    if not isinstance(config, dict):
        raise ValueError('project config must be an object')
    resolution = config.get('resolution', {})
    if not isinstance(resolution, dict):
        raise ValueError('project resolution must be an object')
    width, height = resolution.get('width', 1280), resolution.get('height', 720)
    if (type(width) is not int or not 640 <= width <= 7680
            or type(height) is not int or not 360 <= height <= 4320):
        raise ValueError('project resolution must use integer width 640–7680 and height 360–4320')
    return width, height


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


def asset_destination(source: Path, suffix: str) -> str:
    with source.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    return f'prisma_assets/{checksum[:24]}{suffix.lower()}'


def render_script(
    scenes: list[Scene], cues: dict, assets_dir: Path, font: Path | None, *, confine_assets: bool = False,
    asset_resolver=None, source_map=None, config=None, project_title='Prisma',
) -> tuple[str, dict]:
    """Render native Ren'Py statements and collect the files they reference."""
    images = {".png", ".jpg", ".jpeg", ".webp"}
    audio = {".ogg", ".opus", ".mp3", ".wav", ".flac"}
    files = {}
    asset_paths = {}
    positions = resolve_cues(scenes, cues)
    width, height = project_resolution(config)

    def add_asset(source: Path, extensions: set[str], context: str, *, suffix=None) -> str:
        suffix = suffix or source.suffix.lower()
        if suffix not in extensions or not source.is_file():
            raise ValueError(f"{context}: missing or unsupported asset {source}")
        key = (source, suffix)
        if key in asset_paths:
            return quoted(asset_paths[key])
        destination = asset_destination(source, suffix)
        files[destination] = source
        asset_paths[key] = destination
        return quoted(destination)

    def asset(value, extensions, context):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{context}: expected a file path")
        if asset_resolver:
            return add_asset(asset_resolver(value), extensions, context, suffix=Path(value).suffix.lower())
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
        f'define config.name = {quoted(project_title)}',
        "define config.save_directory = None",
        f"define config.screen_width = {width}",
        f"define config.screen_height = {height}",
        "",
        "transform prisma_backdrop:",
        f"    xysize ({width}, {height})",
        '    fit "cover"',
        "    align (0.5, 0.5)",
        "",
        "transform prisma_portrait:",
        f"    ysize {height}",
        '    fit "contain"',
        "    align (0.5, 1.0)",
    ]
    if font is not None:
        font_path = (asset(font.name, {".ttf", ".otf", ".ttc"}, "font") if asset_resolver
                     else add_asset(font, {".ttf", ".otf", ".ttc"}, "font"))
        headers.extend(("style default:", f"    font {font_path}"))
        for style in ("say_dialogue", "say_label"):
            headers.extend((f"style {style}:", f"    font {font_path}"))

    for scene_index, scene in enumerate(scenes):
        label = scene.id.replace(":", "_")
        body.extend((f"# {scene.id} {scene.chapter}", f"label scene_{label}:"))
        scene_cues = positions[scene.id]

        for index, line in enumerate(scene.lines, 1):
            source = (f" (Raw Text line {line.source})" if line.source_kind == "raw" else
                      f" (Scenario line {line.source})" if line.source_kind == "legacy" else "")
            body.append(f"    # {scene.id}/{index}{source}")
            commands = scene_cues.get(str(index), [])
            if source_map is not None:
                source_map.append(dict(script_line=len(body), scene=scene.id, index=index, id=line.id,
                                       chapter=scene.chapter, speaker=line.speaker, text=line.text, cues=commands))
            context = f"scene {scene.id}, item {index}{source}"
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
    if source_map is not None:
        for point in source_map:
            point['script_line'] += len(headers) + 1
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
    draft.add_argument("--line-ids", action="store_true", help="compatibility option; JSON always includes line IDs")
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
                json.dump(scenario, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
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
