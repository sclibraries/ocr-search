# OCR search keyboard and screen-reader pass

Run this against the loopback-only fake UI/API fixture. It serves the plugin's ERB
views and static CSS with invented records and never contacts ArchivesSpace or a
search service.

Start it from the repository root:

```sh
ruby test/fake_ui_server.rb
```

Open the printed `FAKE_UI_URL` in a browser at `/digital-text?q=mascot`. With
JavaScript disabled, use only the keyboard:

1. Tab from the address bar through the search field and Search text button. Continue
to Publication, Date, the year range, Sort, removable filters, and result links.
The focused control should have a visible outline.
2. Press Enter on a publication or decade link. The filtered results should load,
and the selected filter and sort link should expose `aria-current="true"`.
3. Enter a year range and submit Apply years. Use one removable chip, then Clear all.
The first action removes only that filter; Clear all keeps the search text and resets
the filters and sort.
4. Open See all matching pages, then the item title. The item page should show its
API-provided page list and excerpts, the finding-aid link, and no image viewer for
these non-pilot examples.

With a screen reader, check that the Publication, decade and Sort navigation groups
have names, the year inputs are grouped by their legend, the current filter and sort
are announced as current, and errors are announced as alerts. The result count is
static during normal GET navigation, so it has no live-region announcement. The
controller tests render the same templates against a stubbed API and assert these
landmarks, labels, current states and error roles.
