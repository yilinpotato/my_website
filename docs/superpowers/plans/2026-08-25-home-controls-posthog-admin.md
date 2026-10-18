# Public deployment checklist excerpt

Only the non-private deployment checklist is included here. Original planning notes remain in the private archive. Replace project IDs and configuration placeholders before use.

### Task 8: Deploy, configure PostHog, and verify end to end

**Files:**
- Create locally only: `site_admin/.env` (ignored, mode `0600`)
- Use: `site_admin/deploy/potatoma-site-admin.service`
- Use: `nginx_site_common.conf`

**Interfaces:**
- Consumes all prior tasks.
- Produces live `/site-admin/`, live homepage controls, PostHog events, and 90-day reports.

- [ ] **Step 1: Run the complete pre-deploy test suite**

```bash
node --test tests/*.test.js
node --test site_admin/tests/admin_js.test.js
/home/myl/my_website/site_admin/venv/bin/python -m unittest discover -s site_admin/tests -p 'test_*.py' -v
(cd qq_bot && /home/myl/my_website/venv/bin/python -m unittest -v test_jm_auth.py)
/home/myl/my_website/site_admin/venv/bin/python -m py_compile site_admin/*.py
/home/myl/my_website/venv/bin/python -m py_compile qq_bot/jm_bot.py
for file in home-controls.js project-home.js site_admin/static/admin.js new/myproject/static/training.js; do node --check "$file"; done
git diff --check
```

Expected: all tests PASS and all checks exit 0.

- [ ] **Step 2: Create protected runtime configuration**

Create `site_admin/.env` with the user-approved administrator password, a new random 64-hex session secret, the user-provided Personal API Key, organization ID `00000000-0000-0000-0000-000000000000`, project ID `123456`, and `https://us.posthog.com`. Set mode `0600`. Verify `.env` is ignored with `git check-ignore site_admin/.env`.

- [ ] **Step 3: Apply and verify PostHog project settings**

```bash
/home/myl/my_website/site_admin/venv/bin/python site_admin/configure_posthog.py --apply
/home/myl/my_website/site_admin/venv/bin/python site_admin/configure_posthog.py --check
```

Expected: apply reports seven fields updated or already correct; the following check reports no differences.

- [ ] **Step 4: Install the independent service**

These commands require administrator authority:

```bash
sudo install -m 0644 site_admin/deploy/potatoma-site-admin.service /etc/systemd/system/potatoma-site-admin.service
sudo systemctl daemon-reload
sudo systemctl enable --now potatoma-site-admin.service
systemctl is-active potatoma-site-admin.service
```

Expected: `active`. If authority is unavailable, stop here and provide the exact commands to the user; do not expose secrets.

- [ ] **Step 5: Validate and reload Nginx**

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Expected: syntax successful and reload exits 0. Do not reload when `nginx -t` fails.

- [ ] **Step 6: Verify public-site isolation**

Using HTTPS with the production hostname, assert:

- `/`, `/new/`, `/old/`, `/mygo/`, and `/myagents/` return 200.
- `/jm/` retains its existing 302 password gate and accepts its existing password.
- `/qq/callback?challenge=healthcheck` returns `healthcheck`.
- `/site-admin/` redirects to `/site-admin/login` when unauthenticated.
- Wrong admin password returns 401; sixth rapid failure from one IP returns 429.
- Correct admin password returns the dashboard and its Cookie path is `/site-admin`.

- [ ] **Step 7: Verify PostHog ingestion and reporting**

Open the homepage, toggle language/theme, click a project card, enter the manga viewer, and open a known numeric test manga ID only if it does not trigger an unwanted download. Query PostHog for the dedicated test distinct ID and confirm exact allowed properties.

If a manga lookup would start a download, validate `comic_opened` with a synthetic capture event labeled `source="deployment_test"` instead.

Confirm:

- no password/input/file/message properties,
- IP and GeoIP fields are present,
- dashboard summary and details include the test events,
- the admin date selector cannot request over 90 days,
- CSV exports only the filtered rows.

- [ ] **Step 8: Run a final secret and service audit**

```bash
! git grep -n -E 'phx_[A-Za-z0-9]{20,}|SITE_ADMIN_PASSWORD=.+' -- ':!docs/superpowers/plans/*'
git check-ignore -q site_admin/.env
stat -c '%a' site_admin/.env | rg '^600$'
systemctl is-active potatoma-site-admin.service potatoma-new.service potatoma-old.service potatoma-jm.service nginx.service
```

Expected: no committed secrets, `.env` ignored and mode 600, all services active.

- [ ] **Step 9: Commit any verification-only documentation updates**

```bash
git add SERVER_DEPLOY.md
git -c user.name='yilinpotato' -c user.email='maintainer@example.com' commit -m "docs: record analytics deployment verification"
```

Skip this commit when verification produces no documentation change.

---

## Reference Documentation

- PostHog SQL and HogQL access: https://posthog.com/docs/sql
- PostHog Query API: https://posthog.com/docs/api/query
- PostHog API authentication and US/EU hosts: https://posthog.com/docs/api
- PostHog data storage and IP controls: https://posthog.com/docs/privacy/data-storage
- PostHog authorized URLs: https://posthog.com/docs/health-checks/authorized-urls
