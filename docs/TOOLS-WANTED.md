<!-- Generated from a multi-agent gap analysis (5 domain experts) against the
     current 61 tools. Regenerate/curate by hand; not auto-synced. -->
# Tools wanted — the Calc & Writer roadmap

The connector ships **137 tools today** ([full reference](MCP-TOOLS.md)). This
started as a prioritized wish-list of what to add next — **85 proposed tools** from a
domain-by-domain sweep of the LibreOffice UNO API, each mapped to the exact API a
contributor would wrap.

**Status: the roadmap below is essentially shipped.** All 🥇 good-first tools, the
Writer P1/P2/P3 set, the Calc P1/P2/P3 set, and the cross-cutting umbrellas have been
implemented (76 new tools, 61 → 137). Where the doc's "don't build two of these"
umbrella guidance applied, the consolidated tool was built and the overlapping pieces
were skipped: `set_hyperlink` (not `calc_set_hyperlink`/`writer_insert_hyperlink`),
`export_document` (not `writer_export_pdf`), `calc_named_ranges` (not `calc_define_name`),
`writer_bookmarks` (not `writer_insert_bookmark`), `list_styles`+`set_style` (not
`writer_manage_styles`), `protect_document`+`calc_cell_protection` (not `calc_protect_sheet`);
`refresh_fields` is covered by the shipped `writer_update_indexes`+`calc_recalculate`,
and `writer_insert_ole_chart` by `insert_ole_object object=chart`. A handful of
version-sensitive tools (`calc_create_pivot`, `calc_add_scale_format`, `calc_add_sparkline`,
`calc_multiple_operations`, `writer_mail_merge`) are implemented best-effort and still
need a live-office pass. The tables below are kept as the historical spec.

**Want to build one?** Pick an item below, then follow the recipe in
[CONTRIBUTING.md](../CONTRIBUTING.md). Open an issue first so we don't double-build.

**Priorities:** `P1` common need · `P2` frequently useful · `P3` niche.

## 🥇 Good first tools

Single-API, high-value wrappers — the best on-ramps:

`calc_sort_range` · `calc_set_dimensions` · `calc_set_visibility` · `calc_move_sheet` ·
`calc_recalculate` · `calc_delete_comment` · `calc_delete_chart` · `writer_word_count` ·
`writer_read_table` · `writer_get_paragraphs` · `get_document_properties` ·
`set_document_modified`

## Don't build two of these (umbrella tools)

Several proposals overlap — build the **umbrella**, not the pieces:

- **Hyperlinks** → one `set_hyperlink` (Calc cell + Writer text, two code paths). Supersedes `calc_set_hyperlink`, `writer_insert_hyperlink`.
- **Export options** → one `export_document` (PDF page-range/PDF-A/password/quality + CSV delimiter/encoding). Supersedes `writer_export_pdf`.
- **Refresh stale content** → one `refresh_fields` (Writer fields+indexes, Calc recalc). Supersedes `writer_update_indexes`, `calc_recalculate` (still fine as thin Calc-only wrappers).
- **Named ranges** → one `calc_named_ranges` (list/add/delete). Supersedes `calc_define_name`.
- **Bookmarks** → one `writer_bookmarks` (insert/list/delete/get-set). Supersedes `writer_insert_bookmark`.
- **Styles** → `list_styles` (discover) + `set_style` (create/modify), both families. `writer_manage_styles` is the Writer-only combination of these — prefer the neutral pair.
- **Protection** → `protect_document` (Calc sheet + Writer section) paired with `calc_cell_protection` (unlock the *input* cells first — genuinely distinct).


## Calc (39)

### P1 — common need

| Tool | What it does | Key UNO API |
|---|---|---|
| `calc_add_shape` | Draw a rectangle/ellipse/line/textbox on a sheet with fill, line style, and optional caption text. | doc.createInstance('com.sun.star.drawing.RectangleShape'\|'EllipseShape'\|'LineShape'\|'TextShape'); sheet.DrawPage.add(); set Position/Size (1/100mm), FillColor/FillStyle(+FillGradient), LineColor/LineWidth/LineStyle; shape.setString(text) |
| `calc_autofilter` | Turn the AutoFilter dropdown arrows on (or off) for a data range. | com.sun.star.sheet.DatabaseRanges (doc.DatabaseRanges): addNewByName(name, CellRangeAddress) then dbRange.AutoFilter = True/False; removeByName to clear |
| `calc_create_pivot` | Create a pivot table (DataPilot) from a source range with row/column/page/data fields and aggregation functions. | com.sun.star.sheet.XDataPilotTablesSupplier: sheet.DataPilotTables.createDataPilotDescriptor(); set SourceRange, configure getDataPilotFields() with DataPilotFieldOrientation (ROW/COLUMN/PAGE/DATA) + GeneralFunction (SUM/COUNT/AVERAGE/...); insertNewByName(name, outputCellAddress, descriptor) |
| `calc_edit_chart` | Modify an existing chart: title/subtitle, axis titles, legend on/off/position, chart type, per-series colors, data labels. | sheet.Charts.getByName(name).getEmbeddedObject() -> chart XChartDocument: .Title.String, .HasLegend/.Legend.Alignment, .Diagram.HasXAxisTitle/.XAxisTitle.String, .setDiagram(createInstance('com.sun.star.chart.BarDiagram'\|'LineDiagram'\|...)), Diagram.getDataRowProperties(i).FillColor, Diagram.DataCaption |
| `calc_insert_image` | Insert an image file onto a sheet at a given position/size, anchored to a cell or the page. | doc.createInstance('com.sun.star.drawing.GraphicObjectShape'); load bitmap via com.sun.star.graphic.GraphicProvider.queryGraphic(URL); sheet.DrawPage.add(shape); set Position/Size (1/100mm); Anchor = cell for cell-anchoring |
| `calc_named_ranges` | List, add, or delete workbook-level named ranges / defined names. | com.sun.star.sheet.NamedRanges (doc.NamedRanges): getElementNames(), getByName(n).getContent(), addNewByName(name, content, CellAddress refpos, nType), removeByName(name) |
| `calc_position_shape` | Move, resize, re-anchor, or restack (z-order) an existing shape, image, or chart on a sheet. | XShape.setPosition(com.sun.star.awt.Point 1/100mm)/setSize(Size); shape.Anchor = target cell or sheet; z-order via shape ZOrder property / DrawPage XShapes ordering |
| `calc_set_dimensions` 🥇 | Set column widths or row heights (mm) or auto-fit them for a range of columns/rows. | sheet.getColumns()/getRows().getByIndex(i): .Width / .Height in 1/100 mm, .OptimalWidth=True / .OptimalHeight=True for auto-fit |
| `calc_sort_range` 🥇 | Sort a cell range by one or more key columns/rows, ascending or descending, with an optional header row. | XSortable on the cell range: rng.createSortDescriptor() -> set 'SortFields' (seq<com.sun.star.table.TableSortField>), 'ContainsHeader', 'BindFormatsToContent', 'SortAscending'/'Orientation' -> rng.sort(desc) |

### P2 — frequently useful

| Tool | What it does | Key UNO API |
|---|---|---|
| `calc_add_scale_format` | Add a data-bar, 2/3-color-scale, or icon-set conditional format to a range. | com.sun.star.sheet.XConditionalFormats: sheet.ConditionalFormats.createByRange(rangeAddresses); append a ConditionEntry of com.sun.star.sheet.ConditionEntryType.DATABAR/COLORSCALE/ICONSET, populating DataBarData / ColorScaleEntries / IconSetData (type + min/mid/max rules + colors) |
| `calc_add_subtotals` | Apply grouped subtotals (Data > Subtotals): group by a column and sum/count/etc. other columns, or remove them. | com.sun.star.sheet.XSubTotalCalculatable: sheet.createSubTotalDescriptor(true); descriptor.addNew(SubTotalColumn[]{Column,Function}, groupColumn); sheet.applySubTotals(descriptor, replace); sheet.removeSubTotals() |
| `calc_cell_protection` | Set the locked/formula-hidden/hidden protection attributes on a cell range. | rng.CellProtection = com.sun.star.util.CellProtection struct {IsLocked, IsFormulaHidden, IsHidden, IsPrintHidden} |
| `calc_copy_sheet` | Duplicate a sheet within the document, or import a sheet from another open document. | In-doc: doc.getSheets().copyByName(src, dest, nIndex). Cross-doc: com.sun.star.sheet.XSpreadsheets2 destDoc.getSheets().importSheet(srcDoc, srcSheetName, nIndex) |
| `calc_define_name` | Create or delete a named range / named expression for use in formulas, charts, and pivot sources. | com.sun.star.sheet.XNamedRanges: doc.NamedRanges.addNewByName(name, content, referenceCellAddress, NamedRangeFlag); removeByName(name) |
| `calc_delete_chart` 🥇 | Remove an embedded chart from a sheet by name. | com.sun.star.sheet.XCharts: sheet.Charts.removeByName(name) |
| `calc_delete_comment` 🥇 | Delete a cell comment/annotation (companion to the existing add/get). | com.sun.star.sheet.XSheetAnnotationsSupplier: locate sheet.Annotations entry by CellAddress, Annotations.removeByIndex(i) |
| `calc_fill_series` | Fill a linear/growth/date series or auto-fill a pattern across a range from seed cells. | com.sun.star.sheet.XCellSeries on the range: fillSeries(FillDirection, FillMode, FillDateMode, step, endValue) or fillAuto(FillDirection, nSourceCount) |
| `calc_format_cells_advanced` | Cell presentation beyond format_range: vertical alignment, text rotation/orientation, indent, shrink-to-fit, and cell protection (locked/hidden). | com.sun.star.table.CellProperties: VertJustify (CellVertJustify), RotateAngle (1/100 deg)+RotateReference, ParaIndent, ShrinkToFit, CellProtection struct{IsLocked,IsHidden} |
| `calc_get_cell_format` | Read back a cell/range's number-format code, font, colors, alignment, and applied cell style. | cell.NumberFormat -> doc.getNumberFormats().getByKey(key).FormatString; plus CharWeight, CharColor, CharFontName, CharHeight, CellBackColor, HoriJustify, CellStyle |
| `calc_get_conditional_formats` | Read back the conditional formats on a sheet/range: their ranges, operators, thresholds/formulas, and applied styles. | com.sun.star.sheet.XConditionalFormats.getConditionalFormats(); per XConditionalFormat: getRange(), iterate getConditionByIndex -> ConditionEntryType, Operator, Formula1/Formula2, StyleName |
| `calc_get_validation` | Read back the data-validation rule on a range (type, list entries, input/error messages). | rng.Validation: read .Type (ValidationType), getFormula1()/getFormula2(), ShowInputMessage, InputTitle/InputMessage, ShowErrorMessage, ErrorTitle/ErrorMessage, ShowList |
| `calc_goal_seek` | Solve for the input-cell value that makes a formula cell reach a target result, and optionally write it back. | com.sun.star.sheet.XGoalSeek: doc.seekGoal(formulaCellAddress, variableCellAddress, targetValueString) -> GoalResult{Result, Divergence}; write Result into the variable cell |
| `calc_group_shapes` | Group several shapes into one object, or ungroup an existing group. | com.sun.star.drawing.XShapeGrouper: build com.sun.star.drawing.ShapeCollection of named shapes, DrawPage.group(collection); DrawPage.ungroup(groupShape) |
| `calc_list_charts` | List embedded charts on a sheet with name, source ranges, diagram type, and position/size. | com.sun.star.sheet.XChartsSupplier: enumerate sheet.Charts; per chart getRanges(), getEmbeddedObject().Diagram service name, bounding rect |
| `calc_move_sheet` 🥇 | Reorder an existing sheet to a new 0-based position. | com.sun.star.sheet.XSpreadsheets: doc.getSheets().moveByName(name, nDestIndex) |
| `calc_page_setup` | Set Calc page style: paper size, orientation, margins, scaling/fit-to-pages, and centering. | doc.getStyleFamilies().getByName('PageStyles').getByName(sheet.PageStyle): IsLandscape, Width/Height, {Top,Bottom,Left,Right}Margin (1/100mm), PageScale, ScaleToPagesX/Y, CenterHorizontally/CenterVertically |
| `calc_protect_sheet` | Protect or unprotect a sheet (optionally with a password) and report its protection state. | com.sun.star.util.XProtectable on the sheet: sheet.protect(password), sheet.unprotect(password), sheet.isProtected() |
| `calc_recalculate` 🥇 | Force a recalculation (soft or hard) of the spreadsheet after bulk formula writes. | com.sun.star.sheet.XCalculatable: doc.calculateAll() (hard) / doc.calculate() (dirty-only); optionally set IsIterationEnabled/IterationCount for circular refs |
| `calc_refresh_pivot` | List, refresh, or delete existing pivot tables after their source data changes. | com.sun.star.sheet.XDataPilotTables: getElementNames() to list; getByName(name).refresh(); removeByName(name) |
| `calc_set_hyperlink` | Put a real hyperlink in a cell — external URL, mailto, or internal sheet/cell link — with display text. | com.sun.star.text.TextField.URL: cell.getText().insertTextContent() of a URL field with URL (http:/mailto:/#Sheet.A1) + Representation |
| `calc_set_print_area` | Define (or clear) the print range(s) for a sheet and set repeating title rows/columns. | com.sun.star.sheet.XPrintAreas on the sheet: setPrintAreas(seq<CellRangeAddress>), getPrintAreas(), setTitleRows(CellRangeAddress)+setPrintTitleRows(True), setTitleColumns(...) |
| `calc_set_visibility` 🥇 | Hide or show a span of rows or columns. | sheet.getColumns()/getRows().getByIndex(i).IsVisible = True/False |
| `calc_standard_filter` | Apply a criteria filter (e.g. column > 5, equals text) that hides non-matching rows. | com.sun.star.sheet.XSheetFilterable: desc = rng.createFilterDescriptor(True); desc.FilterFields = seq<com.sun.star.sheet.TableFilterField>; desc.ContainsHeader; rng.filter(desc) |

### P3 — niche

| Tool | What it does | Key UNO API |
|---|---|---|
| `calc_add_sparkline` | Add in-cell sparklines (line/column/stacked) driven by a data range. | com.sun.star.sheet.XSparklineGroups (LO 7.5+): cellRange.getSparklineGroups().addSparklines(sourceRange, targetRange); SparklineGroup props Type (Line/Column/Stacked) + colors |
| `calc_apply_cell_style` | Apply a named cell style (e.g. 'Good', 'Heading 1') to a range, or read the style currently applied. | rng.CellStyle = 'Good' (read cell.CellStyle); manage/create via doc.getStyleFamilies().getByName('CellStyles') |
| `calc_group_outline` | Group/ungroup rows or columns into a collapsible outline, or show/hide the detail. | com.sun.star.sheet.XSheetOutline on the sheet: group(CellRangeAddress, TableOrientation), ungroup(...), showDetail/hideDetail(CellRangeAddress), autoOutline(range), clearOutline() |
| `calc_multiple_operations` | Build a what-if data table (Data > Multiple Operations) over a formula against row/column input cells. | com.sun.star.sheet.XMultipleOperation: targetRange.setTableOperation(formulaRange, TableOperationMode.COLUMN/ROW/BOTH, columnInputCell, rowInputCell) |
| `calc_remove_duplicates` | Remove duplicate rows in a range based on all or selected key columns, keeping the first occurrence. | Read rng.getDataArray(), dedupe on chosen key columns in Python preserving order, write survivors back and clearContents() on the freed tail rows |
| `calc_transpose` | Copy a range to a target cell with rows and columns swapped. | Read source.getDataArray(), transpose in Python, write to target CellRange via getCellRangeByPosition(...).setDataArray() (no single UNO transpose call; insertContents lacks a transpose flag) |

## Writer (30)

### P1 — common need

| Tool | What it does | Key UNO API |
|---|---|---|
| `writer_apply_list` | Turn matched (or all) paragraphs into a bulleted or numbered list, with level, start value and restart-numbering control. | para.NumberingRules = doc.createInstance('com.sun.star.text.NumberingRules') (or set ParaStyleName 'List Number'/'List Bullet'); para.NumberingLevel, ParaIsNumberingRestart, NumberingStartValue |
| `writer_delete_object` | Delete a graphic, text frame, draw shape, or section by name. | getObject.getText().removeTextContent(obj) for anchored content, or doc.getDrawPage().remove(shape) for draw shapes |
| `writer_edit_table` | Edit an existing Writer table: merge a cell range, insert/delete rows or columns at an index, set column widths, and set cell background/borders. | TextTable.createCursorByCellName + cursor.mergeRange()/splitRange(); table.Rows.insertByIndex/removeByIndex, Columns.*; TableColumnSeparators; cell.BackColor / cell TableBorder |
| `writer_insert_field` | Insert a dynamic text field (page number, page count, date/time, doc title, or a mail-merge database field) at a match/selection/end, or into the header/footer. | doc.createInstance('com.sun.star.text.TextField.PageNumber' \| 'PageCount' \| 'DateTime' \| 'DocInfo.Title' \| 'Database'), inserted via text.insertTextContent() |
| `writer_insert_hyperlink` | Insert linked text (label + URL) at the caret/end, or hyperlink an existing search match, with optional target frame. | cursor.HyperLinkURL / HyperLinkName / HyperLinkTarget on the inserted or selected text range (com.sun.star.text.TextRange props) |
| `writer_insert_toc` | Insert a Table of Contents / alphabetical / illustration index built from headings, at the caret, a search match, or document start. | doc.createInstance('com.sun.star.text.ContentIndex') (or DocumentIndex / IllustrationIndex), set Title/Level/CreateFromOutline, text.insertTextContent(cursor, idx, False), then idx.update() |
| `writer_list_objects` | Enumerate all floating objects — graphics, text frames, draw shapes, embedded/OLE objects — with name, type, anchor, position and size (mm). | doc.getGraphicObjects(), doc.getTextFrames(), doc.getDrawPage(), doc.getEmbeddedObjects(); read Name/AnchorType/Position/Size |
| `writer_manage_styles` | List existing paragraph/character styles, create a new one (parent + basic char/para formatting), or apply a named character style to a search match. | doc.StyleFamilies.getByName('ParagraphStyles'\|'CharacterStyles'); doc.createInstance('com.sun.star.style.ParagraphStyle'\|'CharacterStyle'), set ParentStyle + props, family.insertByName(name, style); apply via cursor.ParaStyleName / CharStyleName |
| `writer_set_image_layout` | Set anchor type (as-char/at-para/at-page), text wrap, and horizontal/vertical orientation & position of an existing image or frame. | TextGraphicObject/TextFrame props AnchorType (com.sun.star.text.TextContentAnchorType), TextWrap (com.sun.star.text.WrapTextMode), HoriOrient/VertOrient, HoriOrientPosition/VertOrientPosition |
| `writer_update_indexes` | Refresh ALL tables of contents/indexes AND all dynamic fields so page numbers, ToC entries and dates stop being stale. | doc.getDocumentIndexes().refresh() + doc.getTextFields().refresh() |

### P2 — frequently useful

| Tool | What it does | Key UNO API |
|---|---|---|
| `writer_add_section` | Insert a named regular text section, optionally multi-column and/or write-protected, wrapping appended text. | doc.createInstance('com.sun.star.text.TextSection'); set TextColumns (com.sun.star.text.TextColumns) and IsProtected/ProtectionKey |
| `writer_bookmarks` | Bookmark lifecycle in one tool: insert (at search/selection/end), list (names + anchored text), delete, and get/set the text spanned by a named bookmark. | doc.createInstance('com.sun.star.text.Bookmark'); doc.getBookmarks().getByName(name); bookmark.getAnchor() cursor for get/setString |
| `writer_export_pdf` | Export the Writer doc to PDF with options: page range, PDF/A archival mode, image quality, and open/permissions password. | doc.storeToURL(url, (FilterName='writer_pdf_Export', FilterData=[PageRange, SelectPdfVersion, Quality, EncryptFile, DocumentOpenPassword ...])) |
| `writer_get_paragraphs` 🥇 | List body paragraphs as [{index, text, style, is_heading}] so callers can target a paragraph by index or applied style instead of guessing a unique search string. | doc.getText().createEnumeration(); per paragraph read getString() + ParaStyleName + OutlineLevel |
| `writer_insert_bookmark` | Insert a named bookmark at a text match, the selection, or document end (cross-reference / TOC / mail-merge navigation anchor). | doc.createInstance('com.sun.star.text.Bookmark'); set Name; text.insertTextContent(range, bookmark, cover) |
| `writer_insert_cross_reference` | Insert a cross-reference field that shows a target's page number, number, or text — pointing at a bookmark, heading, or reference mark (optionally creating the mark). | doc.createInstance('com.sun.star.text.ReferenceMark') to set a mark; doc.createInstance('com.sun.star.text.textfield.GetReference'), set ReferenceFieldSource/ReferenceFieldPart/SourceName, insertTextContent; refresh via getTextFields().refresh() |
| `writer_insert_footnote` | Insert a footnote or endnote (kind=footnote\|endnote) with body text, anchored at a search match, the selection, or the caret. | doc.createInstance('com.sun.star.text.Footnote' \| 'com.sun.star.text.Endnote'), text.insertTextContent(cursor, note, False), note.getText().insertString(...) |
| `writer_insert_shape` | Draw a rectangle, ellipse, line, or custom shape at a position/size with fill and line style, optionally with text. | doc.createInstance('com.sun.star.drawing.RectangleShape'\|'EllipseShape'\|'LineShape'\|'CustomShape'); doc.getDrawPage().add(shape); set FillColor/LineColor/LineWidth |
| `writer_insert_text_frame` | Insert a floating text frame (text box) at a given position/size with anchor, wrap, border and background, optionally pre-filled with text. | doc.createInstance('com.sun.star.text.TextFrame'); set Size/AnchorType/HoriOrient/VertOrient/TextWrap, insert via text.insertTextContent(), fill via frame.getText() |
| `writer_mail_merge` | Run a mail merge over Database fields already in the document from a data source (CSV/spreadsheet/registered DB), emitting a merged file, PDF, or printer output. | smgr.createInstance('com.sun.star.text.MailMerge'); set DocumentURL/DataSourceName/CommandType/Command/OutputType (com.sun.star.text.MailMergeType)/OutputURL; .execute(()) |
| `writer_read_table` 🥇 | Read an existing Writer table (by name or index) back as a 2-D grid of cell values/strings. | doc.getTextTables().getByName()/getByIndex(); TextTable.getCellByPosition(c,r).getString()/getValue() over Rows/Columns counts |
| `writer_set_paragraph_text` | Replace the text of a paragraph identified by 0-based index (pairs with get_paragraphs) or at a named bookmark. | walk doc.getText().createEnumeration() to the Nth paragraph, create a cursor over it, cursor.setString(newText) |
| `writer_track_changes` | Manage tracked changes: enable/disable recording, accept-all, reject-all, or list pending redlines (author, type, text). | doc.setPropertyValue('RecordChanges', bool); enumerate doc.getRedlines(); accept/reject via dispatch '.uno:AcceptAllTrackedChanges' / '.uno:RejectAllTrackedChanges' |
| `writer_word_count` 🥇 | Return document statistics: word, character (with/without spaces), paragraph and page counts. | doc.WordCount / ParagraphCount / CharacterCount supplementary properties (com.sun.star.text.TextDocument), page count via doc.CurrentController.PageCount |

### P3 — niche

| Tool | What it does | Key UNO API |
|---|---|---|
| `writer_insert_horizontal_rule` | Insert a horizontal divider line at the caret/end (paragraph bottom-border rule). | insert an empty paragraph and set its BottomBorder (com.sun.star.table.BorderLine2) / ParaStyleName 'Horizontal Line' |
| `writer_insert_ole_chart` | Embed a chart OLE object in the Writer document fed by an inline data table. | doc.createInstance('com.sun.star.text.TextEmbeddedObject') with CLSID of the chart component; set the chart's data via its Diagram/DataProvider |
| `writer_redact` | Black-out occurrences of a search term (or regex) as redaction rectangles across the document. | dispatch '.uno:Redaction' / '.uno:AutoRedactDoc', or overlay filled draw rectangles on matched text ranges via getBounds() |
| `writer_set_page_background` | Set a page background color or image (with tiling/position) on a page style. | PageStyle props BackColor / BackTransparent / BackGraphic / BackGraphicLocation |
| `writer_set_watermark` | Add or remove a text watermark (e.g. DRAFT/CONFIDENTIAL) with font, color, angle and transparency across all pages. | .uno:Watermark dispatch with Text/Font/Angle/Transparency/Color args via com.sun.star.frame.DispatchHelper (or per-page-style header fill-shape fallback) |
| `writer_spellcheck` | Run the spell checker over the document and return flagged words with locations and suggestions. | com.sun.star.linguistic2.SpellChecker via ServiceManager; iterate words with com.sun.star.i18n.BreakIterator, spell.isValid()/spell() for suggestions |

## Cross-cutting (Calc & Writer) (16)

### P1 — common need

| Tool | What it does | Key UNO API |
|---|---|---|
| `export_document` | Store to a path with real filter options the current save_document can't pass: PDF (page range, PDF/A-1, open password, image quality, export comments/bookmarks) and CSV (field/text delimiter, encoding, quote-all, which sheet). | storeToURL(url, [FilterName, FilterData=Sequence<PropertyValue>]) for PDF (pdfexport props: PageRange, SelectPdfVersion, EncryptFile/DocumentOpenPassword, Quality) and FilterOptions token (e.g. '44,34,76,1,,,true') for the Text CSV filter |
| `get_document_properties` 🥇 | Read document metadata: title/author/subject/keywords/description, created/modified dates + editor, word/page/cell statistics, and all custom user-defined properties. | XDocumentPropertiesSupplier.getDocumentProperties() (Title/Author/Subject/Keywords/Description/CreationDate/ModificationDate/DocumentStatistics) + .UserDefinedProperties (XPropertySet) |
| `set_document_properties` | Set standard metadata (title/subject/keywords/description) and add/update/remove custom user-defined properties. | XDocumentProperties setters + UserDefinedProperties (XPropertyContainer.addProperty / XPropertySet.setPropertyValue / removeProperty) |

### P2 — frequently useful

| Tool | What it does | Key UNO API |
|---|---|---|
| `bind_document_event` | Bind (or clear) a macro/script to a document-level event such as OnSave, OnLoad, OnModifyChanged, OnPrint, OnFocus. | XEventsSupplier.getEvents().replaceByName('OnSave', [EventType='Script', Script='vnd.sun.star.script:Lib.Mod.Sub?language=Basic&location=document']) ; empty seq clears |
| `dispatch_uno` | Execute an arbitrary .uno: command (e.g. .uno:Undo, .uno:GoToCell, .uno:DataSort, .uno:InsertPagebreak) against the active frame with optional named args. | com.sun.star.frame.DispatchHelper.executeDispatch(frame, '.uno:Cmd', '', 0, args) |
| `document_undo` | Undo, redo, clear, or query the undo stack of the active document. | XUndoManagerSupplier.getUndoManager() -> XUndoManager.undo()/redo()/clear()/isUndoPossible() (enterUndoContext/leaveUndoContext to batch) |
| `list_styles` | List the names (and key attributes) of every style in a family: paragraph, character, cell, page, frame, numbering, or graphics. | doc.getStyleFamilies().getByName('ParagraphStyles'\|'CellStyles'\|'PageStyles'\|...) -> enumerate names + props |
| `protect_document` | Set or remove structural protection: Calc workbook-structure and/or per-sheet protection with an optional password; Writer protect/unprotect all text sections. | Calc: XProtectable.protect(pwd)/unprotect(pwd)/isProtected() on the doc and on each sheet; Writer: doc.getTextSections() each has IsProtected / ProtectionKey |
| `refresh_fields` | Recompute stale content after programmatic edits: refresh Writer text fields (dates, page/word counts, cross-refs, TOC) and/or Calc formulas and linked ranges. | Writer: XTextFieldsSupplier.getTextFields().refresh() + XDocumentIndexesSupplier index.update(); Calc: XCalculatable.calculateAll() + updateLinks() |
| `set_document_modified` 🥇 | Read the dirty flag and optionally clear it (mark saved) or force it set. | XModifiable.isModified() / setModified(bool) |
| `set_hyperlink` | Attach a clickable hyperlink to a Calc cell (or range) or to Writer text matching a search string, with optional display text. | Insert a com.sun.star.text.TextField.URL (URL/Representation) over the cell's/paragraph's text; Writer inserts into the found text range |
| `set_style` | Create or modify a named style in a family (font, size, color, background, borders, spacing, parent style), reusable across cells/paragraphs. | family = doc.getStyleFamilies().getByName(fam); family.insertByName(name, createInstance('com.sun.star.style.*Style')) or set props on existing; ParentStyle for inheritance |

### P3 — niche

| Tool | What it does | Key UNO API |
|---|---|---|
| `get_signatures` | Report digital-signature status: whether the document is signed, signature validity, signer, and signing date. | com.sun.star.security.DocumentDigitalSignatures / doc.hasValidSignatures / verifyDocumentContentSignatures() -> SignatureInformation[] |
| `insert_ole_object` | Embed an OLE/embedded object — a Math formula, an embedded spreadsheet in Writer, or a linked/embedded sub-document — at a Writer position or Calc cell anchor. | Writer: createInstance('com.sun.star.text.TextEmbeddedObject') with CLSID + insertTextContent; Calc: DrawPage.add(OLE2Shape) with CLSID |
| `list_embedded_objects` | List embedded images and OLE/embedded objects with name, type, size, and anchor/position (Writer graphics + embedded objects; Calc DrawPage graphics/OLE). | Writer: XTextGraphicObjectsSupplier.getGraphicObjects() + XTextEmbeddedObjectsSupplier.getEmbeddedObjects(); Calc: iterate sheet DrawPage for GraphicObjectShape/OLE2Shape |
| `set_view_zoom` | Set the window zoom level/type (percent, page-width, whole-page) and optionally the default view scale saved with the document. | doc.getCurrentController().ZoomValue / ZoomType (com.sun.star.view.DocumentZoomType) |

---
_85 proposed tools — Calc 39, Writer 30, Cross-cutting 16. Generated 2026-07; curate freely._
