# MCP tool reference

All **61 tools** of the `libreoffice` MCP server (v0.6.4), generated from
`mcp/libreoffice_mcp.py`'s `TOOL_DEFS`. Regenerate with the snippet in
`docs/DEVELOPMENT.md` after adding tools.

## Status & selection

| Tool | Description |
|---|---|
| `lo_status` | Check the LibreOffice connection and list open documents. |
| `list_documents` | List the documents currently open in LibreOffice. |
| `lo_screenshot` | Save a PNG screenshot of the LibreOffice WINDOW itself (PrintWindow — captures the real GUI rendering even when the window is behind others; PDF export can differ from the screen, e.g. form controls on RTL sheets). Windows-only. Returns the saved file path. |
| `get_current_selection` | Get the user's current selection: a Calc cell range (with data) or the selected Writer text. |

## Document lifecycle

| Tool | Description |
|---|---|
| `create_document` | Create and open a new empty document ('calc' spreadsheet or 'writer' text document). |
| `open_document` | Open a document file (ods/xlsx/csv/odt/docx/...) in LibreOffice. |
| `save_document` | Save the active document. With 'path': save-as (format from extension or explicit 'format': ods/xlsx/csv/odt/docx/txt). 'format':'pdf' exports a PDF copy. Without 'path': save in place. |
| `close_document` | Close the active document, optionally saving it first (save=true needs an existing file location). |

## Calc data

| Tool | Description |
|---|---|
| `calc_read_range` | Read a Calc cell range as a 2-D array of values. |
| `calc_write_range` | Write a 2-D array of values into a Calc range (dimensions must match the range). |
| `calc_get_formulas` | Read a Calc range as formulas (e.g. '=SUM(A1:A3)') instead of computed values. |
| `calc_set_formulas` | Write a 2-D array of formula strings (or literals) into a Calc range; dimensions must match. |
| `calc_clear_range` | Clear the contents of a Calc range (values, text, formulas; optionally formatting too). |
| `calc_copy_range` | Copy a Calc range (values, formulas, formatting) to a target cell, optionally on another sheet. |
| `calc_find_replace` | Find & replace cell text in one sheet, or in every sheet when 'sheet' is omitted. Returns the replacement count. |
| `calc_get_used_range` | Get the used (non-empty) area of a sheet as an A1 range with its size; optionally include the data. |
| `calc_insert_rows` | Insert empty rows at a 0-based row index (existing rows shift down). |
| `calc_delete_rows` | Delete rows starting at a 0-based row index. |
| `calc_insert_columns` | Insert empty columns at a 0-based column index (existing columns shift right). |
| `calc_delete_columns` | Delete columns starting at a 0-based column index. |

## Calc sheets

| Tool | Description |
|---|---|
| `calc_list_sheets` | List the sheet names of the active spreadsheet and which one is active. |
| `calc_add_sheet` | Add a new sheet, optionally at a 0-based position (default: at the end). |
| `calc_delete_sheet` | Delete a sheet by name (refuses to delete the last remaining sheet). |
| `calc_rename_sheet` | Rename a sheet. |

## Calc presentation

| Tool | Description |
|---|---|
| `calc_format_range` | Format a Calc range: bold/italic/underline, font name/size/color, background color, wrap, horizontal alignment, number format code (e.g. '0.00%', '#,##0.00'), auto-fit columns. |
| `calc_merge_cells` | Merge (merge=true, default) or unmerge (merge=false) a Calc range. |
| `calc_create_chart` | Create an embedded chart from a data range. Types: column, bar, line, pie, area, scatter. |
| `calc_select_range` | Select a range in the LibreOffice window (activates the sheet and highlights the range for the user). |

## Calc conditional formatting & comments

| Tool | Description |
|---|---|
| `calc_add_conditional_format` | Add a conditional format to a range: when a cell meets the condition, a style with the given formatting is applied. Operators: '>', '>=', '<', '<=', '==', '!=', 'between' (value+value2), 'not_between', 'formula' (value is a formula that must be non-zero). Give at least one of background_color/font_color/bold/italic. Stacks with existing conditions unless replace_existing=true. |
| `calc_clear_conditional_formats` | Remove all conditional formats from a Calc range. |
| `calc_add_comment` | Add (or replace) a cell comment/annotation on a single cell. |
| `calc_get_comments` | List cell comments on one sheet, or across all sheets if 'sheet' is omitted: [{sheet, cell, author, text}]. |
| `calc_set_borders` | Draw borders around/through a Calc range (table styling). Full grid by default; outline_only=true draws only the outer border. |

## Writer

| Tool | Description |
|---|---|
| `writer_get_text` | Get the full body text of the active Writer document. |
| `writer_replace_selection` | Replace the current Writer selection with text (or insert at the caret if nothing is selected). |
| `writer_append_text` | Append text at the end of the Writer document ('\n' becomes a paragraph break). new_paragraph=false continues the last paragraph. |
| `writer_insert_heading` | Append a heading paragraph (styles 'Heading 1'..'Heading 6') at the end of the document. |
| `writer_find_replace` | Find & replace text across the Writer document. Returns the replacement count. |
| `writer_format_text` | Apply character formatting (bold/italic/underline/font/size/color) to every match of a search string. |
| `writer_insert_table` | Insert a table at the end of the Writer document, optionally filled with data (rows of strings/numbers). |
| `writer_insert_image` | Insert an image file at the end of the Writer document (size in mm; defaults to the image's own size). |
| `writer_insert_page_break` | Insert a page break at the end of the Writer document. |
| `writer_get_outline` | List the document's headings as an outline: [{level, text}, ...]. |

## Writer comments & conditional sections

| Tool | Description |
|---|---|
| `writer_add_comment` | Add a comment/annotation. Anchors to the first match of 'search' if given, else to the current selection, else at the document end. |
| `writer_get_comments` | List the document's comments: [{author, text, anchor, resolved}]. |
| `writer_add_conditional_section` | Writer's analog of conditional formatting: append text wrapped in a named CONDITIONAL SECTION that is HIDDEN when 'condition' evaluates true (LibreOffice field syntax, e.g. '1==1', 'user_field=="x"'). The condition is evaluated by Writer's layout when the document is viewed/printed. Set visible=false to hide the section immediately regardless of condition. |

## Writer paragraph / page / table styling

| Tool | Description |
|---|---|
| `writer_format_paragraph` | Paragraph formatting for Writer. Targets paragraphs matching 'search', or ALL body paragraphs if 'search' is omitted. Set alignment, line spacing (percent, e.g. 150 = 1.5x), space above/below (mm), left/right/first-line indent (mm), and/or a named paragraph style (e.g. 'Quotations', 'Title'). |
| `writer_set_page_style` | Page styling for Writer: paper size (a4/a5/a3/letter/legal, or width_mm+height_mm), orientation (portrait/landscape), page margins (mm), and column count. Applies to the document's page style. |
| `writer_set_header_footer` | Enable/disable and set the text of the Writer page header or footer. |
| `writer_format_table` | Format a Writer table (by name or 0-based index): draw a full-grid border (width in pt + color) and/or style the header row (bold, background color, font color). |

## Form controls (buttons and other ui elements)

| Tool | Description |
|---|---|
| `insert_form_control` | Insert a form control (UI element) into the active Calc sheet or Writer document: a push button, checkbox, text field, label, or dropdown list box. Position and size in mm. For a button, 'url' makes it open a URL/dispatch command when clicked. For a listbox, 'items' are the dropdown entries. |

## Automation & inspection

| Tool | Description |
|---|---|
| `reload_document` | Store, close and reload the active document from disk. THE verification step after shape/macro work: the in-memory model can lie (e.g. form-control shapes are silently dropped by the ODS writer on RTL sheets) — only a reload shows what actually serialized. Reloads with macros enabled. |
| `run_macro` | Invoke a macro in the active document and return its result. 'name' is 'Library.Module.Sub' (document Basic), 'Module.Sub' (Standard library), or a full vnd.sun.star.script: URI. |
| `calc_list_shapes` | List everything on a sheet's DrawPage: shape names, types, positions/sizes (mm), text, OnClick script, and whether each is a form control. Use to verify buttons/shapes really exist where you think they do. |
| `calc_delete_shape` | Delete shape(s) with the given name from a sheet's DrawPage. |
| `calc_set_active_sheet` | Activate a sheet in the LibreOffice window and optionally select AND scroll to a cell (plain select() does not scroll the viewport). |
| `calc_sheet_properties` | Read and optionally set per-sheet properties: rtl (right-to-left layout — set BEFORE placing shapes, coordinates mirror), visible (hide/show), freeze_rows/freeze_cols (frozen panes). Omitted properties are left unchanged; the reply reports the current state. |
| `calc_set_validation` | Cell validity for a range: 'list' shows a dropdown (blocking wrong entries unless blocking=false), 'hint' shows an on-select help message, 'clear' removes validation. List and hint can combine. |
| `basic_module` | Manage the active document's embedded Basic: action 'list' (libraries + modules with sizes), 'get' (module source), 'set' (create/replace module source). After 'set', invoke a no-op Sub via run_macro as a compile check — one syntax error silently disables the whole module. |
| `inspect_ods` | Regex-search inside the SAVED file's zip entries (content.xml by default) — the ground truth of what serialized, independent of the in-memory model. Defaults to the active document's file. |
| `uno_exec` | Escape hatch: run a short Python snippet against the live UNO bridge. In scope: ctx, smgr, desktop, doc (active document), uno. Printed output is returned as 'stdout'; assign to a variable named `result` to return a JSON value. Use when no dedicated tool fits. |
