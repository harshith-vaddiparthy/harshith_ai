"""Controller for the /ai command center page."""

import frappe

PRIVILEGED_ROLES = {"System Manager", "Course Creator", "Moderator", "LMS Admin"}


def get_context(context):
	context.no_cache = 1

	# Anonymous -> bounce to login, return to /ai afterwards.
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/ai"
		raise frappe.Redirect

	roles = set(frappe.get_roles(frappe.session.user))
	context.is_privileged = bool(roles & PRIVILEGED_ROLES)
	context.full_name = (
		frappe.get_value("User", frappe.session.user, "full_name")
		or frappe.session.user
	)
	context.has_ai = bool((frappe.conf.get("anthropic_api_key") or "").strip())
	context.csrf_token = frappe.sessions.get_csrf_token()
	return context
