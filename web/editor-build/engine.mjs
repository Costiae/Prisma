import {basicSetup, EditorView} from 'codemirror';
import {Compartment, EditorState} from '@codemirror/state';
import {Decoration, MatchDecorator, ViewPlugin, keymap} from '@codemirror/view';
import {completeFromList} from '@codemirror/autocomplete';
import {indentWithTab} from '@codemirror/commands';
import {openSearchPanel} from '@codemirror/search';
import {python} from '@codemirror/lang-python';
import {json} from '@codemirror/lang-json';

const rpyCommands = ['label', 'jump', 'call', 'return', 'menu', 'scene', 'show', 'hide', 'with', 'voice',
  'play music', 'play sound', 'stop music', 'define', 'default', 'image', 'transform', 'screen', 'init python'];
const matchCommand = new MatchDecorator({
  regexp: /^\s*(?:label|jump|call|return|menu|scene|show|hide|with|voice|play|stop|define|default|image|transform|screen|init)\b/g,
  decoration: Decoration.mark({class: 'ide-rpy-keyword'}),
});
const renpy = ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = matchCommand.createDeco(view); }
  update(update) { this.decorations = matchCommand.updateDeco(update, this.decorations); }
}, {decorations: (plugin) => plugin.decorations});

export function openEditor(parent, {onChange, onPosition, onSave, onProjectSearch, labels}) {
  const editing = new Compartment();
  const nonce = document.querySelector('meta[name="prisma-style-nonce"]').content;
  const common = [basicSetup, EditorState.tabSize.of(4), EditorView.cspNonce.of(nonce),
    EditorView.contentAttributes.of({'aria-label': '工程源码'}),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) onChange(update.state.doc.toString());
      if (update.docChanged || update.selectionSet) {
        const head = update.state.selection.main.head;
        const line = update.state.doc.lineAt(head);
        onPosition(line.number, head - line.from + 1);
      }
    }),
    keymap.of([
      {key: 'Mod-s', run: () => { onSave(); return true; }},
      {key: 'Mod-Shift-f', run: () => { onProjectSearch(); return true; }},
      indentWithTab,
    ])];
  function create(name, source) {
    const language = /\.(rpy|rpym|py)$/i.test(name) ? python() : /\.json$/i.test(name) ? json() : [];
    const script = /\.rpym?$/i.test(name) ? [renpy, EditorState.languageData.of(() => [{
      autocomplete: (context) => {
        const before = context.state.doc.lineAt(context.pos);
        const candidates = /^\s*(?:jump|call)\s+/.test(before.text.slice(0, context.pos - before.from))
          ? labels() : rpyCommands;
        return completeFromList(candidates)(context);
      },
    }])] : [];
    return EditorState.create({doc: source, extensions: [...common, language, script, editing.of(EditorState.readOnly.of(false))]});
  }
  const view = new EditorView({parent, state: create('', '')});
  return {
    create,
    state: () => view.state,
    show: (state) => view.setState(state),
    readOnly(value) {
      if (view.state.readOnly !== value) view.dispatch({effects: editing.reconfigure(EditorState.readOnly.of(value))});
    },
    find: () => openSearchPanel(view),
    reveal(number, column = 1, size = 0) {
      const line = view.state.doc.line(Math.max(1, Math.min(number, view.state.doc.lines)));
      const start = Math.max(line.from, Math.min(line.from + column - 1, line.to));
      view.dispatch({selection: {anchor: start, head: Math.min(start + size, line.to)}, scrollIntoView: true});
      view.focus();
    },
    insert(snippet, inline) {
      if (inline) view.dispatch(view.state.replaceSelection(snippet));
      else {
        const line = view.state.doc.lineAt(view.state.selection.main.from);
        const indent = line.text.match(/^[ \t]*/)[0] || '    ';
        view.dispatch({changes: {from: line.from, insert: `${indent}${snippet}\n`}, scrollIntoView: true});
      }
      view.focus();
    },
  };
}
