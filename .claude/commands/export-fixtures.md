Export Frappe fixtures for the `memora_admin` app from the live site DB into `memora_admin/fixtures/*.json`.

Run from the bench root:

```bash
cd /home/corex/aurevia-bench && bench --site x.conanacademy.com export-fixtures --app memora_admin
```

This refreshes the JSON files for every entry in the `fixtures` hook in `memora_admin/hooks.py` (Workspace, Default Workspace Sidebar, Memora Lesson Stage Settings, etc.).

Report the per-doctype "Exporting ..." lines from stdout. If any doctype reports zero records or an error, flag it.
