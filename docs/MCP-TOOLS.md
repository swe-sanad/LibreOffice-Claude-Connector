# MCP tool reference

All **212 tools** of the `libreoffice` MCP server (v0.9.6), generated from
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
| `create_document` | Create and open a new empty document ('calc' spreadsheet, 'writer' text document, 'impress' presentation, or 'draw' drawing). |
| `open_document` | Open a document file (ods/xlsx/csv/odt/docx/...) in LibreOffice. |
| `save_document` | Save the active document. With 'path': save-as (format from extension or explicit 'format': ods/xlsx/csv/odt/docx/txt). 'format':'pdf' exports a PDF copy. Without 'path': save in place. |
| `close_document` | Close a document, optionally saving it first (save=true needs an existing file location). Targets a SPECIFIC doc by 'index'/'title'/'url' (recommended when several are open — focus alone can close the wrong one); defaults to the active document. |

## Calc data

| Tool | Description |
|---|---|
| `calc_read_range` | Read a Calc cell range as a 2-D array of values. |
| `calc_write_range` | Write a 2-D array of values into a Calc range (dimensions must match the range). |
| `calc_get_formulas` | Read a Calc range as formulas (e.g. '=SUM(A1:A3)') instead of computed values. |
| `calc_set_formulas` | Write a 2-D array of formula strings (or literals) into a Calc range; dimensions must match. Formulas may use ',' argument separators regardless of the document's locale (auto-normalized). The reply flags any resulting error cells in 'errors' (and 'error_scan' if the range was too large to verify). |
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
| `writer_find_replace` | Find & replace text across the Writer document. Keeps the formatting of what it replaced: a match spanning several formatting runs (part bold, part not) would otherwise come back chopped along the OLD run boundaries — the replacement now takes the formatting of the match's first character. Set preserve_formatting=false for LibreOffice's raw behaviour. With regex=true, 'search' is an ICU regular expression and $1..$n backreferences work in 'replace'. |
| `writer_format_text` | Apply character formatting (bold/italic/underline/font/size/color) to every match of a search string. |
| `writer_insert_table` | Insert a table, optionally filled with data (rows of strings/numbers). By default appends at the document end; give 'search' to place it right after the first paragraph containing that text, or 'after_index' to place it after a 0-based body-paragraph index. |
| `writer_insert_image` | Insert an image file at the end of the Writer document (size in mm; defaults to the image's own size). |
| `writer_insert_page_break` | Insert a page break at the end of the Writer document. |
| `writer_get_outline` | List the document's headings/subheadings as an outline: [{level, text, index, style}, ...]. 'level' is the outline depth (1 = heading, 2 = subheading, 3 = sub-subheading, ...); 'index' is the body-paragraph index for targeting with writer_format_paragraph / writer_apply_style / writer_move_paragraphs. |

## Writer comments & conditional sections

| Tool | Description |
|---|---|
| `writer_add_comment` | Add a comment/annotation. Anchors to the first match of 'search' if given, else to the current selection, else at the document end. |
| `writer_get_comments` | List the document's comments: [{author, text, anchor, resolved}]. |
| `writer_add_conditional_section` | Writer's analog of conditional formatting: append text wrapped in a named CONDITIONAL SECTION that is HIDDEN when 'condition' evaluates true (LibreOffice field syntax, e.g. '1==1', 'user_field=="x"'). The condition is evaluated by Writer's layout when the document is viewed/printed. Set visible=false to hide the section immediately regardless of condition. |

## Writer paragraph / page / table styling

| Tool | Description |
|---|---|
| `writer_format_paragraph` | Paragraph formatting for Writer. Targets body paragraphs by 0-based 'start'/'count' (the index space writer_get_paragraphs reports), else paragraphs matching 'search', else ALL body paragraphs. Set alignment, line spacing (percent, e.g. 150 = 1.5x), space above/below (mm), left/right/first-line indent (mm), and/or a named paragraph style (e.g. 'Quotations', 'Title') — e.g. restyle one heading by index with start + style_name. |
| `writer_set_page_style` | Page styling for Writer: paper size (a4/a5/a3/letter/legal, or width_mm+height_mm), orientation (portrait/landscape), page margins (mm), and column count. Applies to the document's page style. |
| `writer_set_header_footer` | Enable/disable and set the text of the Writer page header or footer. |
| `writer_format_table` | Format a Writer table (by name or 0-based index): draw a full-grid border (width in pt + color) and/or style the header row (bold, background color, font color). |

## Form controls (buttons and other ui elements)

| Tool | Description |
|---|---|
| `insert_form_control` | Insert a form control into the active Calc sheet or Writer document — the whole Form menu. Position and size in mm. For a button, 'url' opens a URL/dispatch command when clicked; listbox/combobox take 'items'; the numeric family (numeric, currency, formatted, date, time) takes value/min/max/decimals. 'required' and 'readonly' apply wherever the control supports them. Export with export_document form_fields=true to turn these into fillable PDF fields. (Image Control and Table Control are database-bound and need a data source, so they are not offered here.) |

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
| `writer_list_objects` | Enumerate objects in the active Writer doc — graphics, text frames, embedded/OLE objects, and draw shapes (rectangle/ellipse/line/text) — with name, type, anchor, and size (mm). Discovery companion to writer_read_table / writer_get_paragraphs. |
| `writer_set_paragraph_text` | Replace the text of the body paragraph at a 0-based 'index' (the index space writer_get_paragraphs reports). Single paragraph — newlines are not turned into paragraph breaks. |
| `writer_set_text_direction` | Set text writing direction to 'rtl' (Arabic/Hebrew) or 'ltr'. Default flips the WHOLE document: every body paragraph, every table-cell paragraph (tables=false to skip), and the page style (page=false to skip). Give 'start'/'count' to flip only a body-paragraph range instead. Also sets paragraph alignment to match (align=false to keep alignment, e.g. a centered title). |
| `writer_delete_paragraphs` | Delete body paragraphs by 0-based index: 'count' paragraphs starting at 'start' (default 1), including their paragraph breaks. The index space is the one writer_get_paragraphs reports. Deleting every paragraph leaves one empty paragraph (Writer requires at least one). |
| `writer_insert_field` | Insert a dynamic field at the document end (or a new trailing paragraph): page_number, page_count, date, time, title, or author. Refresh later with writer_update_indexes. |
| `writer_insert_toc` | Insert a Table of Contents built from heading outline levels, at the document end or (at_start=true) the top. Populated immediately; re-run writer_update_indexes after adding headings. |
| `writer_update_indexes` | Refresh ALL tables of contents/indexes and all dynamic fields (page numbers, dates, counts) so they stop being stale after programmatic edits. |
| `writer_apply_list` | Turn body paragraphs into a bulleted (default) or numbered (ordered=true) list by attaching NumberingRules directly (works regardless of localized list-style names). Targets paragraphs from 'start' (0-based) for 'count' paragraphs; omit count to go to the end. Errors if the range matches no paragraph or none could be changed. |

## Cross-cutting (calc & writer)

| Tool | Description |
|---|---|
| `set_hyperlink` | Attach a clickable hyperlink. Calc: give 'cell' — replaces it with a URL field. Writer: give 'search' — links every matching text range. |
| `export_document` | Store to a path with filter options. format 'pdf' or 'csv'; defaults to the path extension. PDF supports archival (pdfa), ACCESSIBILITY (tagged, pdfua — pair these with set_alt_text or the pictures stay silent), FILLABLE FORMS (form_fields turns Writer form controls into real AcroForm fields a browser can fill and save), and two separate passwords: 'password' locks opening, 'owner_password' restricts what a reader may do (can_print / can_modify / can_copy / can_annotate). |
| `set_document_properties` | Set document metadata — everything in File > Properties > Description, including the Dublin Core fields. title/author/subject/description plus coverage/identifier/rights/source/type (single values) and keywords/contributor/publisher/relation (ARRAYS — these are multi-value in ODF). 'language' is a BCP-47 tag ('ar-LY') and sets the document language a screen reader announces. 'custom' holds user-defined properties ({name: value}; null removes). Note: a PDF's own info panel only carries title/author/subject/keywords — the rest survive in ODF, and in PDF/A's XMP. |
| `document_lifecycle` | START HERE for any document work. Reads the open document and reports which phase it is in — SETUP (title, language, house style), AUTHORING (content, headings, tables), or CLOSING (metadata, alt text, save, export) — with what is already done, what is left, and the exact tool for each remaining step. Also returns 'ask_the_user': what to ask before proceeding. Call it again after finishing a stage, or whenever you are unsure what to do next. Phases are ADVISORY and derived from the document itself, never stored: every tool works in every phase, so if the user asks to export mid-way, just export. |
| `print_settings` | Read or change how a document prints: printer name, paper size, orientation, and the per-application content switches. Writer exposes PrintGraphics/PrintTables/PrintDrawings/PrintControls/PrintPageBackground/PrintBlackFonts/PrintEmptyPages/PrintHiddenText/PrintLeftPages/PrintRightPages/PrintReversed/PrintProspect (booklet)/PrintProspectRTL/...; Calc exposes PrintGrid/PrintHeaders/PrintCharts/PrintObjects/PrintFormulas/PrintNotes/PrintZeroValues/PrintDownFirst/... Call with no arguments to read the current state and the list valid for this document. |
| `set_alt_text` | Give an image or shape alternative text — the 'Alt Text' a screen reader announces, and what makes a tagged PDF genuinely accessible instead of merely structured. Set 'name' to target one object (writer_list_figures / calc_list_shapes give the names), or omit it to apply to every image and shape. decorative=true marks it as ornamental so assistive tech skips it. |
| `writer_content_control` | Insert a Word-compatible content control (Form > Content Controls): rich_text, plain_text, checkbox, dropdown, combobox, date or picture. Unlike form controls these sit IN the text flow rather than floating over it, survive round-tripping to .docx, and can be bound to XML data via 'xpath'. Wrap existing text with 'search', or append with 'text'. |
| `list_styles` | List style names by family: 'paragraph', 'character', 'cell', 'page', 'frame', 'numbering', ... Omit 'family' for all families. in_use_only filters to styles actually applied. |
| `set_style` | Create or modify a named style in a family (paragraph/character/cell/page/frame). Sets font/size/color/background, optional 'parent' (inherit-from) and 'follow_style' (next-paragraph style, e.g. a heading followed by body text). Reusable across cells/paragraphs. |
| `protect_document` | Set/remove protection. Calc: a 'sheet' protects that sheet, else the workbook structure; optional 'password'. Writer: toggles IsProtected on all text sections. protect=false unprotects. |
| `dispatch_uno` | Execute an arbitrary .uno: command against the active frame. This is the widest escape hatch there is: EVERY menu item and toolbar button in LibreOffice is a .uno: command, including many with no model-level API at all — so when no dedicated tool fits, this usually still can. Examples: '.uno:Undo', '.uno:GoToCell' (args {Nr:'B7'}), '.uno:InsertPagebreak', '.uno:Deselect', '.uno:RecalcPivotTable', '.uno:SelectAll', '.uno:FreezePanes', '.uno:SpellDialog'. It drives the GUI, so it acts on the CURRENT selection/view — set that up first (e.g. calc_select_range). |
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
| `writer_edit_table` | Edit an existing Writer table (by 'name' or 0-based 'index'): insert/delete rows/columns (at_row/at_column), merge a cell range ('A1:B2'), and set a cell's background color and/or text ('cell' + 'background_color'/'text') — editing a cell after insert. |
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

## Menu coverage: table / format / style / form / tools

| Tool | Description |
|---|---|
| `writer_sort_table` | Sort a Writer table's data rows by one key column (0-based 'key_column'), ascending or 'descending'. 'has_header' (default true) keeps row 0 pinned. Numeric-aware. Target by 'name' or 0-based 'index'. |
| `writer_change_case` | Change letter case: mode upper/lower/title/sentence. Targets text matching 'search', else a body-paragraph range ('start'/'count', default all). Case only — no effect on Arabic. |
| `writer_apply_style` | Apply a named style to text. kind 'paragraph' (default): target a 'search' match or a start/count paragraph range. kind 'character': requires 'search'. The style must already exist (create it with set_style). |
| `form_control` | Manage existing form controls (Writer or Calc). action 'list' returns each control's form/name/type/props; action 'set' updates a control by 'name': label, value, state (0/1/2), enabled, read_only, items (listbox). |
| `writer_set_chapter_numbering` | Turn on heading (chapter) numbering: bind the first 'levels' outline levels (default 3) to a scheme so Heading 1/2/3 auto-number as 1, 1.1, 1.1.1. numbering arabic/roman_upper/roman_lower/letter_upper/letter_lower/none; 'separator' between/after numbers (default '.'). |
| `writer_move_paragraphs` | Reorder body paragraphs: move the block of 'count' (default 1) paragraphs starting at 0-based 'start' to index 'to' (the block lands before the paragraph currently there; to == paragraph count appends at the end). Preserves content and formatting. Indices are the writer_get_paragraphs space. |
| `writer_convert_table` | Convert between a table and text. direction 'to_text': turn a table (by 'name' or 0-based 'index') into rows of paragraphs, cells joined by 'separator' (default tab). direction 'to_table': turn body paragraphs [start, start+count) into a table, splitting each on 'separator' (default tab) into columns. |
| `writer_insert_caption` | Insert an auto-numbering caption, e.g. 'Figure 1 — Site plan'. 'category' names the number sequence (Figure/Table/...; numbers increment across captions sharing a category, and LibreOffice renumbers them automatically). Anchor it to a TABLE or an IMAGE by name — the usual case, and the caption then sits above the table / below the figure by convention — or to a text 'search' match, or append at the end. Use writer_list_tables / writer_list_figures to get the names. |

## Failure recovery

| Tool | Description |
|---|---|
| `lo_health` | Pre-flight check before a risky edit: connection and transport, the call timeout, every open document with whether it has UNSAVED changes and a real file, stale .~lock files left by a crash, pending crash-recovery, and whether AutoSave is on. Returns a 'problems' list and a 'healthy' flag. Call this when something has gone wrong, or before a large/destructive change. |
| `lo_recover` | LibreOffice's crash recovery, driven over UNO instead of the startup dialog. action 'status' (default) reports whether it crashed, what is waiting to be recovered and the AutoSave setting; 'restore' reopens the pending documents; 'discard' permanently destroys that unsaved work and needs confirm=true; 'set_autosave' turns AutoSave on with 'minutes' (0 = off). |
| `document_watch` | Notice when a document changes underneath you — in particular when the USER edits it while Claude is thinking. action 'start' begins watching, 'check' reports how many changes happened and separates OUR edits from the user's, 'stop' ends it, 'list' shows what is watched. Start a watch before a long or multi-step operation, then check before overwriting anything. |
| `checkpoint_document` | Snapshot a document to a side file so a risky edit can be undone. THIS IS THE ONLY ROLLBACK for anything that writes a cell range — LibreOffice does not record bulk range writes for undo, so Ctrl+Z cannot bring those back. action 'create' (default) saves a copy and returns a checkpoint_id, 'list' shows saved checkpoints, 'restore' puts one back (closing and reopening the document; unsaved edits since the checkpoint are lost). |
| `writer_captions` | List or re-word existing captions. action 'list' returns every auto-numbered caption (index, category, number, label) — including ones made with LibreOffice's own Insert > Caption. action 'set' rewrites the LABEL of the caption picked by 'index', 'search' or 'category', leaving the number a live field so renumbering still works. To delete a caption outright use writer_delete_paragraphs. |
| `writer_table_formula` | Set a formula in a Writer table cell and return the computed value. Writer cell-reference syntax, e.g. '=<A1>+<A2>', '=<A1>*2', 'sum <A1:A5>'. Target the table by 'name' or 0-based 'index'. |
| `writer_split_cells` | Split a table cell (or an 'A1:B1' range) into 'into' cells (default 2) along 'columns' (default) or 'rows'. Target the table by 'name' or 0-based 'index'. |
| `writer_clear_formatting` | Remove direct character/paragraph formatting (reset to the underlying style) from text matching 'search', or a body-paragraph range ('start'/'count', default all). |
| `writer_set_line_numbering` | Turn document line numbering on ('enable', default true) or off, and set 'interval' (number every Nth line), 'count_empty_lines', and left 'distance_mm' (Tools > Line Numbering). |
| `set_active_document` | Focus a specific open document so subsequent reads/writes target it — select by 'title' (substring, case-insensitive), 'url' (substring), or 0-based 'index' over the open docs (see list_documents). Fixes focus-stealing that silently redirects writes to the wrong document. |
| `writer_replace_image` | Replace an existing image by 'name': swap its graphic (new 'path') and/or resize it (width_mm/height_mm) in place — e.g. update a logo without rebuilding. Use writer_list_objects to find image names. |
| `writer_repeat_heading_rows` | Make a table's first 'rows' (default 1) repeat as a header on every page the table spans, or turn it off with repeat=false. Target the table by 'name' or 0-based 'index'. |
| `writer_find` | Locate text WITHOUT changing it: returns each matching body paragraph's 0-based index, occurrence count, a snippet, and its style — so you can then target it by index (writer_set_paragraph_text, writer_format_paragraph, writer_delete_paragraphs, ...). Read-only companion to writer_find_replace. |
| `writer_list_tables` | List every table with 0-based index, name, row/column counts, and a header-row preview — discovery for writer_edit_table / writer_sort_table / writer_convert_table / writer_table_formula. |
| `writer_list_figures` | List images/figures with name, size (mm), anchor type, and the anchoring paragraph's text (often the caption/context) — discovery for writer_replace_image / writer_set_image_layout. |
| `writer_set_document_defaults` | Set the document's base typography via the 'Standard' paragraph style: font_name and/or font_size, applied to Western + Complex (RTL/CTL) + Asian scripts so an Arabic base font actually takes effect document-wide. |
| `writer_insert_tab_stops` | Set paragraph tab stops (positions_mm = list of mm) on matched paragraphs ('search') or a body-paragraph range (start/count, default all). align left/right/center/decimal; optional 'fill' char (e.g. '.' for dotted signature lines). |
| `calc_export_range` | Export a cell 'range' (or the sheet's used range if omitted) to a CSV or JSON file at 'path'. format defaults to the path extension; CSV is UTF-8-BOM with an optional 'delimiter'. |
| `batch` | Run several tool calls in one round-trip. 'operations' is a list of {tool, args}; returns each result/error in order. stop_on_error (default true) halts on the first failure. Cuts latency on long multi-step document builds. |

## Upstream-parity: document ops, macros, dispatcher, calc convenience

| Tool | Description |
|---|---|
| `convert` | Headlessly convert document(s) to another format via LibreOffice filters (docx/xlsx->pdf, odt->docx, ...). Give 'path' (one) or 'paths' (many) + target 'to'; outputs land beside each source or in 'output_dir'. Each file is loaded hidden, stored, and closed — the active document is untouched. |
| `merge` | Merge several Writer/text documents into one, in order, with a page break between them; save to 'output'. Text documents only (Calc/PDF merge out of scope). |
| `list_templates` | List document templates under LibreOffice's configured Template paths: [{name, path}] plus the directories scanned. |
| `create_from_template` | Create a new untitled document from a template file (.ott/.ots/...). |
| `run_python_macro` | Invoke a PYTHON macro via the script provider (complements run_macro's Basic). 'name' is a full vnd.sun.star.script: URI, or 'file.py$function' resolved at 'location' (user/share/document; default user). Returns the macro's return value. |
| `list_macros` | Discover macros: document Basic libraries -> modules, plus user Python script files. Best-effort (application Basic isn't always enumerable). |
| `dispatch` | Escape hatch to EVERY tool this server has, including the ones not advertised in the current tier: run any of them by name — {tool, args}. Omit 'tool' (or use 'list'/'help') for the full catalog of names + one-line usage. Use this whenever the advertised set has no tool for the job — the catalog is the authoritative list of what is possible. |
| `calc_statistics` | Descriptive statistics over the NUMERIC cells in a Calc range: count, sum, mean, min, max, median, and population stdev. Text/empty cells ignored. |
| `read_spreadsheet` | Read every sheet's used range at once: {sheet_name: 2-D values} — a whole workbook in one call instead of one calc_read_range per sheet. |

## Everyday composites

| Tool | Description |
|---|---|
| `calc_overview` | Map the workbook cheaply before reading it: per sheet the used range, its row/column count, a few sample rows and whether row 1 looks like headers. Output stays small on a huge file — prefer this over read_spreadsheet to get your bearings. |
| `calc_format_table` | Make a data range look like a finished table in ONE call: bold coloured header, full border grid, auto-fitted columns and a frozen header row. Presets: clean (grey header), report (blue header), financial (blue header + #,##0.00 on the body). Defaults to the sheet's used range. |
| `calc_clean_data` | Tidy a pasted or imported range: trim stray whitespace, turn numeric-looking text into real numbers, and drop fully empty rows. Formula cells are never rewritten. Defaults to the sheet's used range. NOTE: LibreOffice does not record bulk range writes for undo, so Ctrl+Z restores the deleted rows but not the trimmed values — say what will change before running it on data the user cannot re-import. |
| `writer_format_document` | Make a Writer document presentable in ONE call: base font and size (all scripts, so Arabic/CTL takes effect), line spacing and page margins. Presets: report (sans 11pt, 20mm, 1.15), essay (serif 12pt, 1in, double), letter (serif 12pt, 25mm, single). |

## Everyday tools borrowed from the sibling projects

| Tool | Description |
|---|---|
| `calc_import_csv` | Import a CSV/TSV file INTO the open spreadsheet at a target cell — unlike open_document, which opens the file as its own separate document. Delimiter is auto-detected. Fields are written as text or numbers, never as formulas, so a field starting with '=' cannot execute. |
| `calc_detect_errors` | Find every broken formula in the workbook — #REF!, #DIV/0!, #NAME?, #VALUE!, circular references — reporting the sheet, cell, what the error means and the formula that caused it. Scans all sheets unless 'sheet' is given. Use this when a spreadsheet 'stopped working' or shows error markers. |
| `list_recent_documents` | List the documents from LibreOffice's File > Recent Documents, newest first, with title and file path — so a user who says 'open the essay I was working on' can be offered the right file without knowing where it lives. |
| `print_document` | Send a document to a PHYSICAL printer — this consumes real paper. Only call it when the user has actually asked to print, and confirm the printer and page range first if there is any doubt. Targets a specific open doc by index/title/url, else the active one. |
| `writer_resolve_comment` | Mark Writer comment(s) resolved or unresolved — the write side of what writer_get_comments reports. Pick by 'index' (as listed by writer_get_comments), or by 'search' (comment-text substring) / 'author' to resolve every match. Needs LibreOffice 7.1+. |

## Impress (presentations) — slides addressed by 1-based index

| Tool | Description |
|---|---|
| `impress_overview` | Read the presentation: slide count and, per slide, its 1-based index, layout, title, body text length, and whether it has speaker notes. The 'orient yourself' tool for a deck — call it first. |
| `impress_add_slide` | Add a slide and apply a layout. 'after' (1-based) inserts the new slide right after that slide; omit to append at the end. 'layout' picks the placeholders: title_subtitle, title_content, two_content, title_only, or blank. Returns the new slide's 1-based number. |
| `impress_read_slide` | Read one slide in full: its layout, title, body bullets (each with its indent level), the names of any other shapes, and speaker notes. Address it by 1-based 'slide'. |
| `impress_set_title` | Set the title placeholder of slide 'slide' (1-based) to 'text'. The slide needs a layout that has a title (all but 'blank'). |
| `impress_set_content` | Fill the content/outline placeholder of slide 'slide' (1-based) with bullet points. 'bullets' is a list of strings, or {'text','level'} objects where level 0 is a top bullet and 1+ indents it. Needs a content layout (e.g. title_content). |
| `impress_set_notes` | Set the speaker notes of slide 'slide' (1-based) to 'text'. Notes are what the presenter sees, not the audience. |
| `impress_insert_image` | Insert an image from a local file 'path' onto slide 'slide' (1-based). Position/size in millimetres (x_mm/y_mm/width_mm/height_mm); size defaults to the image's own dimensions. |
| `impress_insert_shape` | Add an auto shape (rectangle, ellipse, line, text) to slide 'slide' (1-based) with optional 'text' and 'fill_color' (hex like '#4472C4'). Position/size in millimetres. |
| `impress_insert_text_box` | Add a free-floating text box to slide 'slide' (1-based) holding 'text', positioned/sized in millimetres. For text outside the layout placeholders. |
| `impress_set_layout` | Change the autolayout of slide 'slide' (1-based) to 'layout' (title_subtitle, title_content, two_content, title_only, blank). Reflows the placeholders; existing placeholder text is kept where a matching box remains. |
| `impress_delete_slide` | Delete slide 'slide' (1-based). Refuses to delete the last remaining slide. |
| `impress_duplicate_slide` | Duplicate slide 'slide' (1-based); the copy is inserted immediately after it. Returns the new slide's 1-based number. |
| `impress_set_transition` | Set the slide-change transition on slide 'slide' (1-based) or every slide ('all':true). 'type': none, fade, wipe, push, cover, uncover, dissolve, wheel, cut. 'duration' is the effect length (seconds); 'advance_secs' auto-advances after N seconds (omit = advance on click). |
| `impress_export_slides` | Render slides to image files in directory 'dir' — one file per slide (slide-01.png, ...). 'format': png, svg, or jpg. Exports all slides unless 'slide' (1-based) is given. This is real rendering, not available to .pptx file writers. |
| `impress_insert_table` | Insert a table on slide 'slide' (1-based). Give 'rows'+'cols', or a 'data' grid (list of rows) to size and fill it in one call. Position/size in millimetres. |
| `impress_insert_chart` | Insert a data chart on slide 'slide' (1-based). 'chart_type': column, bar, line, area, pie. 'data' is a grid whose first row is the series headers and first column is the category labels (e.g. [['','2023','2024'],['APAC',10,14],['EMEA',8,9]]). Optional 'title'. Position/size in millimetres. |
| `impress_slideshow` | Control the on-screen slideshow: action 'start' (optionally 'from_slide', 1-based), 'stop', or 'status'. Starting launches the show in the LibreOffice window, so it needs a GUI session (not a headless office). Returns whether a show is running. |

## Draw (vector drawings) — pages addressed by 1-based index

| Tool | Description |
|---|---|
| `draw_overview` | Read a Draw document: page count and, per page, its 1-based index, name, and shape count. The 'orient yourself' tool for a drawing. |
| `draw_read_page` | List the shapes on Draw page 'page' (1-based): each shape's index, name, kind, and any text. |
| `draw_add_page` | Append a new page to the Draw document, optionally naming it. Returns the new page's 1-based number. |
| `draw_insert_shape` | Add an auto shape (rectangle, ellipse, line, text) to Draw page 'page' (1-based, default 1) with optional 'text' and 'fill_color' (hex). Position/size in millimetres. |
| `draw_insert_text_box` | Add a text box holding 'text' to Draw page 'page' (1-based, default 1). Position/size in millimetres. |
| `draw_insert_image` | Insert an image from local file 'path' onto Draw page 'page' (1-based, default 1). Position/size in millimetres; size defaults to the image's own dimensions. |
| `draw_insert_connector` | Draw a connector line on Draw page 'page' (1-based, default 1) from (x1_mm,y1_mm) to (x2_mm,y2_mm). Optionally glue its ends to shapes by 1-based shape index (start_shape/end_shape) so the connector follows them. Draw's diagramming primitive. |
