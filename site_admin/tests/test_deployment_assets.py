import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]


def documented_nginx_context() -> str:
    runbook = (REPOSITORY / "SERVER_DEPLOY.md").read_text()
    marked = runbook.split(
        "<!-- site-admin-nginx-context:start -->",
        1,
    )[1].split("<!-- site-admin-nginx-context:end -->", 1)[0]
    return marked.split("```nginx", 1)[1].split("```", 1)[0].strip()


def nginx_server_blocks(context: str) -> tuple[str, ...]:
    blocks = []
    for match in re.finditer(r"(?m)^server\s*\{", context):
        depth = 0
        for index in range(match.start(), len(context)):
            character = context[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(context[match.start():index + 1])
                    break
        else:
            raise AssertionError("unterminated documented Nginx server block")
    return tuple(blocks)


class DeploymentAssetTests(unittest.TestCase):
    def test_common_nginx_include_has_no_site_admin_routes(self):
        common = (REPOSITORY / "nginx_site_common.conf").read_text()

        self.assertNotIn("/site-admin", common)
        self.assertIn("__SITE_ANALYTICS_CONFIG__", common)

    def test_https_admin_include_has_static_before_exact_proxy_contract(self):
        https = (REPOSITORY / "nginx_site_admin_https.conf").read_text()
        static = https.index("location /site-admin/static/")
        proxy = https.index("location /site-admin/", static + 1)

        self.assertLess(static, proxy)
        self.assertEqual(https.count("location /site-admin/static/"), 1)
        self.assertEqual(https.count("location /site-admin/ {"), 1)
        for directive in (
            "alias /home/myl/my_website/site_admin/static/;",
            "expires 1h;",
            "proxy_pass http://127.0.0.1:5011/;",
            "proxy_redirect off;",
            "proxy_set_header Host $host;",
            "proxy_set_header X-Real-IP $remote_addr;",
            "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "proxy_set_header X-Forwarded-Proto $scheme;",
            "proxy_set_header X-Forwarded-Prefix /site-admin;",
            "proxy_read_timeout 45s;",
            "proxy_send_timeout 45s;",
        ):
            self.assertIn(directive, https)

    def test_runbook_documents_the_complete_three_server_topology(self):
        runbook = (REPOSITORY / "SERVER_DEPLOY.md").read_text()
        context = documented_nginx_context()
        blocks = nginx_server_blocks(context)

        self.assertEqual(len(blocks), 3)
        tls = next(block for block in blocks if "listen 443 ssl;" in block)
        potatoma_http = next(
            block
            for block in blocks
            if "listen 80;" in block and "potatoma.me www.potatoma.me" in block
        )
        default_http = next(
            block for block in blocks if "listen 80 default_server;" in block
        )

        common_include = "include /home/myl/my_website/nginx_site_common.conf;"
        admin_include = (
            "include /etc/nginx/snippets/potatoma-site-admin-https.conf;"
        )
        self.assertEqual(tls.count(common_include), 1)
        self.assertEqual(tls.count(admin_include), 1)
        self.assertEqual(default_http.count(common_include), 1)
        self.assertNotIn(admin_include, default_http)
        self.assertNotIn(common_include, potatoma_http)
        self.assertNotIn(admin_include, potatoma_http)
        self.assertEqual(context.count(admin_include), 1)
        self.assertEqual(context.count(common_include), 2)

        self.assertEqual(
            potatoma_http.count("location ^~ /.well-known/acme-challenge/"),
            1,
        )
        self.assertEqual(
            re.findall(
                r"location(?:\s+\^~)?\s+([^\s{]+)\s*\{",
                potatoma_http,
            ),
            ["/.well-known/acme-challenge/", "/"],
        )
        self.assertIn("try_files $uri =404;", potatoma_http)
        self.assertIn("return 301 https://$host$request_uri;", potatoma_http)
        self.assertNotIn("proxy_pass", potatoma_http)
        self.assertNotIn("alias ", potatoma_http)
        self.assertNotIn("/site-admin", potatoma_http)
        self.assertIn(
            "sudo install -o root -g root -m 0644 nginx_site_admin_https.conf",
            runbook,
        )
        self.assertIn(
            "/etc/nginx/snippets/potatoma-site-admin-https.conf",
            runbook,
        )

    def test_documented_tls_full_topology_passes_nginx_validation(self):
        nginx = shutil.which("nginx")
        openssl = shutil.which("openssl")
        self.assertIsNotNone(nginx, "nginx is required for topology validation")
        self.assertIsNotNone(openssl, "openssl is required for TLS validation")

        with tempfile.TemporaryDirectory() as temporary:
            fixture_root = Path(temporary)
            certificate = fixture_root / "certificate.pem"
            private_key = fixture_root / "private-key.pem"
            generated = subprocess.run(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=potatoma.me",
                    "-keyout",
                    str(private_key),
                    "-out",
                    str(certificate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)

            context = documented_nginx_context()
            context = context.replace(
                "listen 443 ssl;",
                "listen 18443 ssl;",
            ).replace(
                "listen 80 default_server;",
                "listen 18080 default_server;",
            ).replace(
                "listen 80;",
                "listen 18080;",
            ).replace(
                "/etc/letsencrypt/live/potatoma.me/fullchain.pem",
                str(certificate),
            ).replace(
                "/etc/letsencrypt/live/potatoma.me/privkey.pem",
                str(private_key),
            ).replace(
                "/home/myl/my_website/nginx_site_common.conf",
                str(REPOSITORY / "nginx_site_common.conf"),
            ).replace(
                "/etc/nginx/snippets/potatoma-site-admin-https.conf",
                str(REPOSITORY / "nginx_site_admin_https.conf"),
            )
            configuration = fixture_root / "nginx.conf"
            configuration.write_text(
                f"pid {fixture_root / 'nginx.pid'};\n"
                f"error_log {fixture_root / 'error.log'};\n"
                "events {}\n"
                "http {\n"
                "  access_log off;\n"
                f"{context}\n"
                "}\n"
            )

            validated = subprocess.run(
                [nginx, "-t", "-p", f"{fixture_root}/", "-c", str(configuration)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                validated.returncode,
                0,
                validated.stdout + validated.stderr,
            )

    def test_admin_service_and_commands_use_only_the_dedicated_venv(self):
        service = (
            REPOSITORY / "site_admin/deploy/potatoma-site-admin.service"
        ).read_text()
        runbook = (REPOSITORY / "SERVER_DEPLOY.md").read_text()
        plan = (
            REPOSITORY
            / "docs/superpowers/plans/2026-08-25-home-controls-posthog-admin.md"
        ).read_text()
        task_eight = plan.split(
            "### Task 8: Deploy, configure PostHog, and verify end to end",
            1,
        )[1]

        dedicated = "/home/myl/my_website/site_admin/venv/bin"
        shared = "/home/myl/my_website/venv/bin"
        self.assertIn(f"ExecStart={dedicated}/gunicorn -w 1", service)
        self.assertIn('--timeout 45', service)
        self.assertNotIn(f"ExecStart={shared}/gunicorn", service)
        self.assertIn("python3 -m venv site_admin/venv", runbook)
        self.assertIn("site_admin/venv/bin/pip install -r site_admin/requirements.txt", runbook)
        self.assertIn(f"{dedicated}/python site_admin/configure_posthog.py --check", runbook)
        self.assertNotIn(f"{shared}/python site_admin/configure_posthog.py", runbook)
        self.assertNotIn("./venv/bin/pip install -r site_admin/requirements.txt", runbook)
        self.assertIn(f"{dedicated}/python -m unittest discover", task_eight)
        self.assertIn("node --test site_admin/tests/admin_js.test.js", task_eight)
        self.assertIn(f"{dedicated}/python site_admin/configure_posthog.py --apply", task_eight)
        self.assertNotIn(f"{shared}/python site_admin/configure_posthog.py", task_eight)

    def test_runbook_grants_and_rolls_back_only_narrow_static_acls(self):
        runbook = (REPOSITORY / "SERVER_DEPLOY.md").read_text()

        for command in (
            "sudo setfacl -m u:www-data:--x /home/myl",
            "sudo setfacl -m u:www-data:--x /home/myl/my_website",
            "sudo setfacl -m u:www-data:--x /home/myl/my_website/site_admin",
            "sudo setfacl -R -m u:www-data:rX /home/myl/my_website/site_admin/static",
            "sudo setfacl -m d:u:www-data:rX /home/myl/my_website/site_admin/static",
            "sudo -u www-data test -r /home/myl/my_website/site_admin/static/admin.css",
            "sudo -u www-data test ! -r /home/myl/my_website/site_admin/.env",
            "sudo setfacl -R -x u:www-data /home/myl/my_website/site_admin/static",
            "sudo setfacl -x u:www-data /home/myl/my_website/site_admin",
            "sudo setfacl -x u:www-data /home/myl/my_website",
            "sudo setfacl -x u:www-data /home/myl",
        ):
            self.assertIn(command, runbook)
        self.assertIn("chmod 600 site_admin/.env", runbook)
        self.assertNotIn("chmod o+x /home/myl", runbook)

    def test_acl_rollback_clears_nested_defaults_and_is_idempotent(self):
        runbook = (REPOSITORY / "SERVER_DEPLOY.md").read_text()
        rollback = runbook.split(
            "撤销仅为后台静态目录添加的 `www-data` ACL",
            1,
        )[1].split("```bash", 1)[1].split("```", 1)[0]
        commands = tuple(line.strip() for line in rollback.splitlines() if line.strip())
        static_root = "/home/myl/my_website/site_admin/static"

        self.assertEqual(
            commands,
            (
                "sudo find /home/myl/my_website/site_admin/static -type d "
                "-exec setfacl -x d:u:www-data {} + 2>/dev/null || true",
                "sudo setfacl -R -x u:www-data "
                "/home/myl/my_website/site_admin/static 2>/dev/null || true",
                "sudo setfacl -x u:www-data "
                "/home/myl/my_website/site_admin 2>/dev/null || true",
                "sudo setfacl -x u:www-data "
                "/home/myl/my_website 2>/dev/null || true",
                "sudo setfacl -x u:www-data /home/myl 2>/dev/null || true",
            ),
        )
        default_cleanup = commands[0]
        self.assertIn("find", default_cleanup)
        self.assertIn("-type d", default_cleanup)
        self.assertIn("-exec setfacl -x d:u:www-data {} +", default_cleanup)
        self.assertTrue(
            all(command.endswith("2>/dev/null || true") for command in commands)
        )
        self.assertNotIn("site_admin/.env", rollback)

        with tempfile.TemporaryDirectory() as temporary:
            fixture_root = Path(temporary)
            static_fixture = fixture_root / "static"
            nested_directory_fixture = (
                static_fixture,
                static_fixture / "vendor",
                static_fixture / "vendor" / "icons",
            )
            for directory in nested_directory_fixture:
                directory.mkdir(exist_ok=True)
            fake_bin = fixture_root / "bin"
            fake_bin.mkdir()
            calls = fixture_root / "setfacl.calls"
            fake_setfacl = fake_bin / "setfacl"
            fake_setfacl.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$ACL_FIXTURE_LOG"\n'
                "exit 1\n"
            )
            fake_setfacl.chmod(0o700)
            environment = dict(
                os.environ,
                PATH=f"{fake_bin}:{os.environ['PATH']}",
                ACL_FIXTURE_LOG=str(calls),
            )
            fixture_commands = tuple(
                command.replace("sudo ", "", 1).replace(
                    static_root,
                    str(static_fixture),
                )
                for command in commands[:2]
            )

            for _ in range(2):
                for command in fixture_commands:
                    result = subprocess.run(
                        ["bash", "-c", command],
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

            default_calls = tuple(
                call
                for call in calls.read_text().splitlines()
                if call.startswith("-x d:u:www-data")
            )
            self.assertEqual(len(default_calls), 2)
            for call in default_calls:
                for directory in nested_directory_fixture:
                    self.assertIn(str(directory), call)


if __name__ == "__main__":
    unittest.main()
