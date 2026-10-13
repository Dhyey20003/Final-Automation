// Excel-like data grid: cell selection, keyboard navigation, inline editing, copy, multi-row paste,
// resizable columns, header sorting and a per-column filter row. Data loading is done by the caller.
import { h, clear, debounce } from './ui.js';

export class DataGrid {
  constructor({ columns, onEdit, onPaste, onSort, onFilter, onOpen, filters = {}, sort = null, dir = 'asc', widths = {}, onWidths }) {
    this.columns = columns;
    this.onEdit = onEdit; this.onPaste = onPaste; this.onSort = onSort; this.onFilter = onFilter; this.onOpen = onOpen;
    this.filters = { ...filters }; this.sort = sort; this.dir = dir; this.widths = { ...widths }; this.onWidths = onWidths;
    this.rows = []; this.sel = null; this.editing = null;
    this.table = h('table', { class: 'dgrid', role: 'grid' });
    this.wrap = h('div', { class: 'dgrid-wrap', tabindex: '0', 'aria-label': 'Employee table. Use arrow keys to move, Enter to edit, Ctrl+V to paste.' }, this.table);
    this.wrap.addEventListener('keydown', (e) => this.onKey(e));
    this.wrap.addEventListener('paste', (e) => this.onPasteEvent(e));
    this.wrap.addEventListener('copy', (e) => this.onCopy(e));
    this.fireFilter = debounce(() => this.onFilter && this.onFilter({ ...this.filters }), 400);
  }

  visibleColumns() { return this.columns.filter((c) => c.visible !== false); }

  setColumns(columns) { this.columns = columns; this.render(); }

  setRows(rows) { this.rows = rows; this.editing = null; if (this.sel && this.sel.r >= rows.length) this.sel = null; this.render(); }

  render() {
    const cols = this.visibleColumns();
    const colgroup = h('colgroup', null, cols.map((c) => h('col', { style: { width: (this.widths[c.key] || c.width || 140) + 'px' } })));
    const head = h('tr', null, cols.map((c, i) => this.headerCell(c, i)));
    const filterRow = h('tr', { class: 'filters' }, cols.map((c) => h('th', { class: c.sticky ? 'sticky' : '' }, this.filterInput(c))));
    const body = h('tbody', null, this.rows.map((row, r) => h('tr', { dataset: { r } }, cols.map((c, ci) => this.cell(row, r, c, ci)))));
    clear(this.table);
    this.table.append(colgroup, h('thead', null, head, filterRow), body);
  }

  headerCell(c, i) {
    const arrow = this.sort === c.key ? (this.dir === 'asc' ? '▲' : '▼') : '';
    const inner = h('div', { class: 'th-inner', title: c.help || c.label }, c.label, arrow ? h('span', { class: 'sort' }, arrow) : null);
    if (c.sortable !== false) inner.addEventListener('click', () => {
      if (this.sort === c.key) this.dir = this.dir === 'asc' ? 'desc' : 'asc'; else { this.sort = c.key; this.dir = 'asc'; }
      this.onSort && this.onSort(this.sort, this.dir);
    });
    const th = h('th', { class: c.sticky ? 'sticky' : '', scope: 'col' }, inner);
    const grip = h('div', { class: 'resizer', title: 'Drag to resize' });
    grip.addEventListener('mousedown', (e) => this.startResize(e, c));
    th.appendChild(grip);
    return th;
  }

  filterInput(c) {
    if (c.filterable === false) return null;
    const val = this.filters[c.key] || '';
    let el;
    if (c.type === 'dropdown' || c.type === 'yesno') {
      const opts = c.type === 'yesno' ? ['Yes', 'No'] : c.options || [];
      el = h('select', { 'aria-label': 'Filter ' + c.label }, h('option', { value: '' }, 'All'), opts.map((o) => h('option', { value: o, selected: o === val }, o)),
        h('option', { value: '(empty)', selected: val === '(empty)' }, '(empty)'));
      el.addEventListener('change', () => { this.filters[c.key] = el.value; this.onFilter && this.onFilter({ ...this.filters }); });
    } else {
      el = h('input', { value: val, placeholder: c.type === 'number' || c.type === 'calculated' ? '>=, <=, =' : 'Filter…', 'aria-label': 'Filter ' + c.label,
        title: 'Type text to match. You can also use =value, >value, <value, empty or !empty.' });
      el.addEventListener('input', () => { this.filters[c.key] = el.value; this.fireFilter(); });
    }
    return el;
  }

  startResize(e, c) {
    e.preventDefault(); e.stopPropagation();
    const startX = e.clientX;
    const start = this.widths[c.key] || c.width || 140;
    const move = (ev) => {
      this.widths[c.key] = Math.max(50, start + ev.clientX - startX);
      const idx = this.visibleColumns().indexOf(c);
      const col = this.table.querySelectorAll('col')[idx];
      if (col) col.style.width = this.widths[c.key] + 'px';
    };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); this.onWidths && this.onWidths({ ...this.widths }); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }

  cell(row, r, c, ci) {
    const editable = c.editable && !row.__readonly;
    const cls = [editable ? 'editable' : 'readonly', c.sticky ? 'sticky' : '', c.type === 'number' || c.type === 'calculated' ? 'num' : '', c.cls || ''];
    if (this.sel && this.sel.r === r && this.sel.c === ci) cls.push('sel');
    const td = h('td', { class: cls.join(' '), dataset: { r, c: ci }, title: c.render ? '' : this.text(row, c) });
    if (this.editing && this.editing.r === r && this.editing.c === ci) td.appendChild(this.editor(row, c));
    else if (c.render) td.appendChild(c.render(row));
    else td.textContent = this.text(row, c);
    td.addEventListener('mousedown', () => { if (!(this.editing && this.editing.r === r && this.editing.c === ci)) this.select(r, ci); });
    td.addEventListener('dblclick', () => this.startEdit(r, ci));
    return td;
  }

  text(row, c) {
    const v = row[c.key];
    if (v === null || v === undefined) return '';
    if (c.format) return c.format(v, row);
    return String(v);
  }

  select(r, c) {
    this.sel = { r, c };
    this.table.querySelectorAll('td.sel').forEach((td) => td.classList.remove('sel'));
    const td = this.table.querySelector(`td[data-r="${r}"][data-c="${c}"]`);
    if (td) { td.classList.add('sel'); td.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    this.wrap.focus({ preventScroll: true });
  }

  startEdit(r, ci, initial) {
    const c = this.visibleColumns()[ci];
    const row = this.rows[r];
    if (!c || !c.editable || !row || row.__readonly) return;
    this.sel = { r, c: ci };
    this.editing = { r, c: ci, initial };
    this.rerenderCell(r, ci);
  }

  rerenderCell(r, ci) {
    const old = this.table.querySelector(`td[data-r="${r}"][data-c="${ci}"]`);
    if (!old) return;
    const c = this.visibleColumns()[ci];
    const fresh = this.cell(this.rows[r], r, c, ci);
    old.replaceWith(fresh);
    const ed = fresh.querySelector('.cell-editor');
    if (ed) { ed.focus(); if (ed.select && this.editing && this.editing.initial === undefined) ed.select(); }
  }

  editor(row, c) {
    let el;
    const cur = row[c.key] ?? '';
    if (c.type === 'dropdown' || c.type === 'yesno') {
      const opts = c.type === 'yesno' ? ['Yes', 'No'] : c.options || [];
      el = h('select', { class: 'cell-editor' }, h('option', { value: '' }, '(blank)'), opts.map((o) => h('option', { value: o, selected: o === cur }, o)));
    } else if (c.type === 'date') {
      el = h('input', { class: 'cell-editor', type: 'date', value: cur });
    } else {
      el = h('input', { class: 'cell-editor', value: this.editing.initial !== undefined ? this.editing.initial : cur });
    }
    const commit = async (move) => {
      if (!this.editing) return;
      const { r, c: ci } = this.editing;
      const value = el.value;
      this.editing = null;
      if (String(value) !== String(cur)) {
        try { await this.onEdit(row, c, value === '' ? null : value); }
        catch (_) { /* caller reports the error and refreshes */ }
      }
      this.rerenderCell(r, ci);
      if (move) this.move(move[0], move[1]);
      this.wrap.focus({ preventScroll: true });
    };
    el.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); commit([1, 0]); }
      else if (e.key === 'Tab') { e.preventDefault(); commit([0, e.shiftKey ? -1 : 1]); }
      else if (e.key === 'Escape') { const { r, c: ci } = this.editing; this.editing = null; this.rerenderCell(r, ci); this.wrap.focus(); }
    });
    el.addEventListener('blur', () => setTimeout(() => { if (this.editing) commit(null); }, 0));
    if (el.tagName === 'SELECT') el.addEventListener('change', () => commit(null));
    return el;
  }

  move(dr, dc) {
    if (!this.sel) return this.select(0, 0);
    const cols = this.visibleColumns().length;
    const r = Math.min(this.rows.length - 1, Math.max(0, this.sel.r + dr));
    const c = Math.min(cols - 1, Math.max(0, this.sel.c + dc));
    this.select(r, c);
  }

  onKey(e) {
    if (this.editing || !this.rows.length) return;
    const k = e.key;
    if (!this.sel && ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Enter'].includes(k)) { e.preventDefault(); return this.select(0, 0); }
    if (!this.sel) return;
    if (k === 'ArrowDown') { e.preventDefault(); this.move(1, 0); }
    else if (k === 'ArrowUp') { e.preventDefault(); this.move(-1, 0); }
    else if (k === 'ArrowLeft') { e.preventDefault(); this.move(0, -1); }
    else if (k === 'ArrowRight' || k === 'Tab') { e.preventDefault(); this.move(0, e.shiftKey && k === 'Tab' ? -1 : 1); }
    else if (k === 'Enter' || k === 'F2') {
      e.preventDefault();
      const c = this.visibleColumns()[this.sel.c];
      if (c.editable) this.startEdit(this.sel.r, this.sel.c);
      else if (this.onOpen) this.onOpen(this.rows[this.sel.r]);
    } else if (k === 'Delete' || k === 'Backspace') {
      const c = this.visibleColumns()[this.sel.c];
      const row = this.rows[this.sel.r];
      if (c.editable && row[c.key] !== null && row[c.key] !== undefined && row[c.key] !== '') {
        e.preventDefault();
        this.onEdit(row, c, null).catch(() => {}).finally(() => this.rerenderCell(this.sel.r, this.sel.c));
      }
    } else if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const c = this.visibleColumns()[this.sel.c];
      if (c.editable && c.type !== 'dropdown' && c.type !== 'date' && c.type !== 'yesno') { e.preventDefault(); this.startEdit(this.sel.r, this.sel.c, k); }
    }
  }

  onCopy(e) {
    if (!this.sel || this.editing) return;
    const c = this.visibleColumns()[this.sel.c];
    e.clipboardData.setData('text/plain', this.text(this.rows[this.sel.r], c));
    e.preventDefault();
  }

  onPasteEvent(e) {
    if (this.editing || !this.sel || !this.onPaste) return;
    const text = e.clipboardData.getData('text/plain');
    if (!text) return;
    e.preventDefault();
    const lines = text.replace(/\r\n/g, '\n').replace(/\n$/, '').split('\n').map((l) => l.split('\t'));
    this.onPaste(this.sel.r, this.sel.c, lines);
  }
}
