# MCP tool reference

All **137 tools** of the `libreoffice` MCP server (v0.8.0), generated from
`mcp/libreoffice_mcp.py`'s `TOOL_DEFS`. Regenerate with the snippet in
`docs/DEVELOPMENT.md` after adding tools.

## Status & selection

| Tool | Description |
|---|---|
| `lo_status` | Check the LibreOffice connection (reports the transport: pipe = agent-acceptor extension, socket = accept flag/auto-launch) and list open documents. |
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

## Good first tools (single-api wrappers)

| Tool | Description |
|---|---|
| `writer_word_count` | Document statistics for the active Writer doc: word, paragraph, character counts and page count. |
| `writer_read_table` | Read an existing Writer table back as a 2-D grid of cell strings. Give 'name' (from writer_list_objects / find) or a 0-based 'index' (default 0). |
| `writer_get_paragraphs` | List body paragraphs as [{index, text, style, is_heading}] so callers can target a paragraph by 0-based index or applied style instead of a unique search string. Index counts only body paragraphs (skips tables/frames). |
| `calc_sort_range` | Sort a cell range by one or more key columns. 'keys' is a list of {column: 0-based offset within the range, descending?, case_sensitive?}. Set has_header to keep the first row in place. |
| `calc_set_dimensions` | Set column widths or row heights (mm) or auto-fit them for a span. Give 'axis' ('columns'\|'rows'), 'start' (0-based), 'count', and either 'size_mm' or 'autofit': true. |
| `calc_set_visibility` | Hide or show a span of rows or columns. Give 'axis' ('columns'\|'rows'), 'start' (0-based), 'count', and 'visible'. |
| `calc_move_sheet` | Reorder an existing sheet to a new 0-based position. |
| `calc_recalculate` | Force a recalculation after bulk formula writes: hard=true (default) recomputes everything, hard=false only dirty cells. |
| `calc_delete_comment` | Delete the cell comment/annotation on a cell (companion to calc_add_comment / calc_get_comments). |
| `calc_delete_chart` | Remove an embedded chart from a sheet by name. |
| `get_document_properties` | Read the active document's metadata: title/author/subject/keywords/description, created/modified dates + editor, statistics, and custom user-defined properties. |
| `set_document_modified` | Read the dirty flag and optionally set it: modified=false marks the document saved, true forces it dirty. Returns the resulting state. |

## Writer p1

| Tool | Description |
|---|---|
| `writer_list_objects` | Enumerate floating objects in the active Writer doc — graphics, text frames, and embedded/OLE objects — with name, anchor type, and size (mm). Discovery companion to writer_read_table / writer_get_paragraphs. |
| `writer_set_paragraph_text` | Replace the text of the body paragraph at a 0-based 'index' (the index space writer_get_paragraphs reports). Single paragraph — newlines are not turned into paragraph breaks. |
| `writer_insert_field` | Insert a dynamic field at the document end (or a new trailing paragraph): page_number, page_count, date, time, title, or author. Refresh later with writer_update_indexes. |
| `writer_insert_toc` | Insert a Table of Contents built from heading outline levels, at the document end or (at_start=true) the top. Populated immediately; re-run writer_update_indexes after adding headings. |
| `writer_update_indexes` | Refresh ALL tables of contents/indexes and all dynamic fields (page numbers, dates, counts) so they stop being stale after programmatic edits. |
| `writer_apply_list` | Turn body paragraphs into a bulleted (default) or numbered (ordered=true) list by applying the 'List Bullet'/'List Number' paragraph style. Targets paragraphs from 'start' (0-based) for 'count' paragraphs; omit count to go to the end. |

## Cross-cutting (calc & writer)

| Tool | Description |
|---|---|
| `set_hyperlink` | Attach a clickable hyperlink. Calc: give 'cell' — replaces it with a URL field. Writer: give 'search' — links every matching text range. |
| `export_document` | Store to a path with filter options. format 'pdf' (page_range, pdfa, quality 0-100, password) or 'csv' (delimiter, quote). Format defaults to the path extension. |
| `set_document_properties` | Set document metadata: title/author/subject/description, keywords (array), and 'custom' user-defined properties ({name: value}; value null removes). |
| `list_styles` | List style names by family: 'paragraph', 'character', 'cell', 'page', 'frame', 'numbering', ... Omit 'family' for all families. in_use_only filters to styles actually applied. |
| `set_style` | Create or modify a named style in a family (paragraph/character/cell/page/frame). Sets font/size/color/background and optional parent. Reusable across cells/paragraphs. |
| `protect_document` | Set/remove protection. Calc: a 'sheet' protects that sheet, else the workbook structure; optional 'password'. Writer: toggles IsProtected on all text sections. protect=false unprotects. |
| `dispatch_uno` | Execute an arbitrary .uno: command against the active frame (e.g. '.uno:Undo', '.uno:GoToCell', '.uno:InsertPagebreak') with optional named args. Escape hatch when no dedicated tool fits. |
| `document_undo` | Undo/redo/clear the active document's undo stack, or just query it (action 'status'). Returns whether undo/redo are possible and the next undo title. |
| `bind_document_event` | Bind (or clear) a Basic/script macro to a document event such as OnSave, OnLoad, OnModifyChanged, OnPrint. Omit 'script' to clear the binding. |
| `set_view_zoom` | Set the window zoom: 'percent' (a number) and/or 'type' (optimal/page_width/whole_page/percent/page_width_exact). |
| `get_signatures` | Report digital-signature status of the saved document: whether it is signed, validity, signer, and signing date. |
| `list_embedded_objects` | List embedded images and OLE objects with name, type, and size (mm). Writer: graphics + embedded objects. Calc: DrawPage graphic/OLE shapes across all sheets. |
| `insert_ole_object` | Embed an OLE object. Give 'object' (math/calc/chart) or a raw 'clsid'. Writer: inserts at the end. Calc: adds to a sheet's DrawPage at the given size. |

## Writer p2/p3

| Tool | Description |
|---|---|
| `writer_delete_object` | Delete a graphic, text frame, embedded object, draw shape, or text section by name. |
| `writer_edit_table` | Edit an existing Writer table (by 'name' or 0-based 'index'): insert/delete rows/columns (at_row/at_column), merge a cell range ('A1:B2'), and set a cell background color. |
| `writer_set_image_layout` | Set anchor (as_char/char/paragraph/page/frame), text wrap (none/through/parallel/dynamic/left/right), and absolute position (x_mm/y_mm) of an existing image or text frame by name. |
| `writer_add_section` | Insert a named text section at the end, optionally multi-column and/or write-protected, wrapping optional text. |
| `writer_bookmarks` | Bookmark lifecycle: action 'list', 'insert' (at a 'search' match or the end), 'delete', 'get' (anchored text), or 'set' (replace anchored text). |
| `writer_insert_cross_reference` | Insert a cross-reference field at the end pointing at a bookmark or reference mark ('target'), showing its page/number/text ('show'). Refreshed on insert. |
| `writer_insert_footnote` | Insert a footnote or endnote (kind) with body text, anchored at a 'search' match or the document end. |
| `writer_insert_shape` | Draw a rectangle/ellipse/line/text shape on the draw page at position/size (mm) with optional fill/line color, caption text, and name. |
| `writer_insert_text_frame` | Insert a floating text frame (text box) at the end with a given size (mm), optionally pre-filled with text and named. |
| `writer_mail_merge` | Run a mail merge over Database fields already in the (saved) document, from a registered 'data_source' + 'command' (table/query name), emitting file/printer/mail output. Requires a registered data source. |
| `writer_track_changes` | Manage tracked changes: action enable/disable recording, accept_all, reject_all, or list/status (returns recording state + pending redlines with author/type/comment). |
| `writer_insert_horizontal_rule` | Insert a horizontal divider line at the document end (a paragraph in the 'Horizontal Line' style). |
| `writer_redact` | Black out every occurrence of a search term (black text on black background). NOTE: visual redaction only — the underlying text still exists in the file. |
| `writer_set_page_background` | Set (color) or clear (clear=true) the page background color on a page style (default 'Standard'). |
| `writer_set_watermark` | Add a text watermark (empty text clears it) with font, angle, transparency (0-100) and color across all pages. |
| `writer_spellcheck` | Spell-check the document body and return flagged words with suggestions. 'language' is a BCP-47 tag (default 'en-US'); 'max_words' caps results. |

## Calc p1/p2/p3

| Tool | Description |
|---|---|
| `calc_add_shape` | Draw a rectangle/ellipse/line/text shape on a sheet at a position (position_cell or x_mm/y_mm) and size (mm), with optional fill/line color, caption text, and name. |
| `calc_insert_image` | Insert an image file onto a sheet at a position (position_cell or x_mm/y_mm) and optional size (mm; defaults to the image's native size). |
| `calc_position_shape` | Move (x_mm/y_mm), resize (width_mm/height_mm) or restack (z_order) an existing shape/image/chart on a sheet by name. |
| `calc_autofilter` | Turn the AutoFilter dropdowns on for a range (enable=true, default) or off (enable=false). |
| `calc_edit_chart` | Modify an existing chart: title, subtitle, legend on/off, x/y axis titles, and chart_type (column/bar/line/area/pie/...). |
| `calc_list_charts` | List embedded charts on a sheet with name, source ranges, and header flags. |
| `calc_named_ranges` | Workbook named ranges: action 'list', 'add' (name + content like 'Sheet1.$A$1:$B$5'), or 'delete'. |
| `calc_create_pivot` | Create a pivot table (DataPilot) from a source range. 'fields' is a list of {field, orientation: row\|column\|page\|data, function: sum\|count\|average\|max\|min}. Output anchored at output_cell. |
| `calc_refresh_pivot` | Existing pivot tables on a sheet: action 'list', 'refresh' (one 'name' or all), or 'delete'. |
| `calc_add_subtotals` | Apply grouped subtotals: group by column 'group_by' (0-based) and aggregate 'columns' (0-based list) with 'function' (sum/count/average/max/min); or remove=true to clear. |
| `calc_goal_seek` | Solve for the variable-cell value that makes a formula cell reach 'target'; writes it back unless apply=false. Returns result + divergence. |
| `calc_fill_series` | Fill a series across a range: direction (down/right/up/left), mode (linear/growth/date/auto), step, and optional end value. |
| `calc_cell_protection` | Set locked/formula-hidden/hidden/print-hidden protection attributes on a range. Only takes effect once the sheet is protected (protect_document). |
| `calc_format_cells_advanced` | Advanced cell presentation: vertical_align (standard/top/center/bottom), rotation (degrees), indent (mm), shrink_to_fit, wrap. |
| `calc_get_cell_format` | Read a cell's number-format code, font, size, weight, colors (hex), horizontal alignment, and applied cell style. |
| `calc_get_conditional_formats` | Read back the conditional formats on a sheet: their ranges and per-condition Formula1/Formula2/StyleName. |
| `calc_get_validation` | Read back the data-validation rule on a range (type, formulas, input/error messages, dropdown flag). |
| `calc_page_setup` | Calc page style: landscape, paper (a4/a5/a3/letter/legal), margins (mm), scale %, fit_pages_x/y, center_h/center_v. |
| `calc_set_print_area` | Define the print range for a sheet (or clear=true), with optional repeating title_rows / title_columns ranges. |
| `calc_standard_filter` | Apply a criteria filter that hides non-matching rows. 'conditions' is a list of {column: 0-based, operator: =\|!=\|>\|>=\|<\|<=, value}. |
| `calc_group_shapes` | Group >=2 named shapes into one ('names' + optional 'group' name), or ungroup=true a group named 'group'. |
| `calc_group_outline` | Row/column outline: action group/ungroup/show/hide over a range (axis rows\|columns), or clear the whole outline. |
| `calc_multiple_operations` | Build a what-if data table over a formula range against column and/or row input cells (mode column/row/both). |
| `calc_remove_duplicates` | Remove duplicate rows in a range (keep first). key_columns (0-based list) restricts the dedupe key; has_header keeps the first row. |
| `calc_transpose` | Copy a range to a target cell with rows and columns swapped (optionally onto another sheet). |
| `calc_apply_cell_style` | Apply a named cell style (e.g. 'Good', 'Heading 1') to a range, or read the current style if 'style' is omitted. |
| `calc_add_sparkline` | Add in-cell sparklines driven by a data range (LibreOffice 7.5+). |
| `calc_add_scale_format` | Add a color-scale or data-bar conditional format to a range (kind colorscale\|databar), with default thresholds/colors. |
| `calc_copy_sheet` | Duplicate a sheet within the document to 'new_name' at an optional 0-based position. |
