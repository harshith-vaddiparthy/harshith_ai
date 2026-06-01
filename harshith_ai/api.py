"""
Harshith AI — API backend.

Two kinds of endpoints:
  1. Stats  -> aggregate REAL data from the LMS doctypes (no AI, always works).
  2. AI     -> call Anthropic's Claude with a tailored prompt. If no API key is
               configured, every AI endpoint returns a graceful, clearly-labelled
               placeholder so the dashboard is fully usable out of the box.

Security: all endpoints require an authenticated session AND a privileged role
(Course Creator / System Manager / LMS Admin). The Anthropic key lives ONLY in
site_config.json server-side and is never sent to the browser.
"""

import json

import frappe
from frappe import _

# Roles allowed to use the command center.
PRIVILEGED_ROLES = {"System Manager", "Course Creator", "Moderator", "LMS Admin"}

# Claude model + endpoint. Opus is the most capable; swap freely.
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-opus-4-8"
ANTHROPIC_VERSION = "2023-06-01"


# --------------------------------------------------------------------------- #
#  Guards
# --------------------------------------------------------------------------- #
def _guard():
	"""Reject anonymous or non-privileged callers."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in to use the AI Command Center."), frappe.PermissionError)
	roles = set(frappe.get_roles(frappe.session.user))
	if not (roles & PRIVILEGED_ROLES):
		frappe.throw(
			_("You need a Course Creator or Admin role to use the AI Command Center."),
			frappe.PermissionError,
		)


def _api_key():
	"""Anthropic key from site config; empty string if unset."""
	return (frappe.conf.get("anthropic_api_key") or "").strip()


def has_ai():
	"""True if a Claude key is configured (drives the 'AI live' badge in UI)."""
	_guard()
	return bool(_api_key())


# --------------------------------------------------------------------------- #
#  Claude call
# --------------------------------------------------------------------------- #
def _call_claude(system, user, max_tokens=1200, temperature=0.7):
	"""
	Call Claude. Returns (text, is_real).
	  is_real=True  -> genuine model output
	  is_real=False -> graceful placeholder (no key / error), clearly labelled
	"""
	key = _api_key()
	if not key:
		return (_placeholder(user), False)

	import requests

	try:
		resp = requests.post(
			ANTHROPIC_URL,
			headers={
				"x-api-key": key,
				"anthropic-version": ANTHROPIC_VERSION,
				"content-type": "application/json",
			},
			json={
				"model": frappe.conf.get("anthropic_model") or ANTHROPIC_MODEL,
				"max_tokens": max_tokens,
				"temperature": temperature,
				"system": system,
				"messages": [{"role": "user", "content": user}],
			},
			timeout=90,
		)
		if resp.status_code != 200:
			frappe.log_error(f"Anthropic {resp.status_code}: {resp.text[:500]}", "harshith_ai")
			return (
				f"⚠️ The AI service returned an error ({resp.status_code}). "
				f"Showing a draft instead.\n\n{_placeholder(user)}",
				False,
			)
		data = resp.json()
		parts = data.get("content") or []
		text = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
		return (text or _placeholder(user), bool(text))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "harshith_ai call_claude")
		return (
			"⚠️ Couldn't reach the AI service just now. Showing a draft instead.\n\n"
			+ _placeholder(user),
			False,
		)


def _placeholder(user_prompt):
	"""Readable stand-in when AI is unavailable."""
	snippet = (user_prompt or "").strip().split("\n")[0][:140]
	return (
		"— AI PREVIEW (placeholder) —\n\n"
		f"Request: {snippet}\n\n"
		"This is a placeholder response. Once your Anthropic API key is active, "
		"this panel returns a real, tailored answer from Claude. Everything else "
		"on the dashboard — your live course and student numbers — is already real."
	)


# --------------------------------------------------------------------------- #
#  STATS  (real LMS data)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def get_dashboard_stats():
	"""Headline KPIs + recent activity, computed from live LMS tables."""
	_guard()

	courses = frappe.get_all(
		"LMS Course",
		fields=["name", "title", "published", "enrollments", "lessons", "rating",
				"paid_course", "course_price", "currency", "creation"],
		order_by="creation desc",
	)
	total_courses = len(courses)
	published = sum(1 for c in courses if c.get("published"))

	total_enrollments = frappe.db.count("LMS Enrollment")
	total_students = frappe.db.count(
		"LMS Enrollment", filters={"member_type": "Student"}
	) or len({e.member for e in frappe.get_all("LMS Enrollment", fields=["member"])})

	total_batches = frappe.db.count("LMS Batch")
	total_quizzes = frappe.db.count("LMS Quiz")
	total_lessons = frappe.db.count("Course Lesson")
	total_certificates = frappe.db.count("LMS Certificate")

	# Average progress across all enrollments.
	progress_rows = frappe.get_all("LMS Enrollment", fields=["progress"])
	avg_progress = round(
		sum((r.get("progress") or 0) for r in progress_rows) / len(progress_rows), 1
	) if progress_rows else 0

	# Estimated revenue from paid-course enrollments.
	revenue = 0.0
	price_by_course = {c["name"]: (c.get("course_price") or 0) for c in courses if c.get("paid_course")}
	if price_by_course:
		paid_enrolls = frappe.get_all(
			"LMS Enrollment",
			filters={"course": ["in", list(price_by_course.keys())]},
			fields=["course"],
		)
		for e in paid_enrolls:
			revenue += float(price_by_course.get(e["course"], 0) or 0)

	# Quiz performance.
	quiz_rows = frappe.get_all("LMS Quiz Submission", fields=["percentage"])
	avg_quiz = round(
		sum((q.get("percentage") or 0) for q in quiz_rows) / len(quiz_rows), 1
	) if quiz_rows else 0

	# Top courses by enrollment.
	top_courses = sorted(
		[{"title": c["title"], "enrollments": c.get("enrollments") or 0,
		  "rating": c.get("rating") or 0, "published": bool(c.get("published"))}
		 for c in courses],
		key=lambda x: x["enrollments"],
		reverse=True,
	)[:5]

	return {
		"kpis": {
			"courses": total_courses,
			"published": published,
			"students": total_students,
			"enrollments": total_enrollments,
			"batches": total_batches,
			"lessons": total_lessons,
			"quizzes": total_quizzes,
			"certificates": total_certificates,
			"avg_progress": avg_progress,
			"avg_quiz": avg_quiz,
			"revenue": round(revenue, 2),
			"currency": (courses[0]["currency"] if courses and courses[0].get("currency") else "USD"),
		},
		"top_courses": top_courses,
		"has_ai": bool(_api_key()),
		"admin": frappe.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
	}


@frappe.whitelist()
def get_recent_activity(limit=8):
	"""Recent enrollments + quiz submissions, merged into one feed."""
	_guard()
	limit = int(limit)
	feed = []

	for e in frappe.get_all(
		"LMS Enrollment",
		fields=["member_name", "course", "creation"],
		order_by="creation desc",
		limit=limit,
	):
		feed.append({
			"type": "enrollment",
			"who": e.get("member_name") or "A student",
			"what": frappe.get_value("LMS Course", e["course"], "title") or e["course"],
			"when": str(e["creation"]),
		})

	for q in frappe.get_all(
		"LMS Quiz Submission",
		fields=["member_name", "quiz_title", "percentage", "creation"],
		order_by="creation desc",
		limit=limit,
	):
		feed.append({
			"type": "quiz",
			"who": q.get("member_name") or "A student",
			"what": f"{q.get('quiz_title') or 'a quiz'} — {round(q.get('percentage') or 0)}%",
			"when": str(q["creation"]),
		})

	feed.sort(key=lambda x: x["when"], reverse=True)
	return feed[:limit]


@frappe.whitelist()
def get_courses_brief():
	"""Lightweight course list for the AI tools' context dropdowns."""
	_guard()
	return frappe.get_all(
		"LMS Course",
		fields=["name", "title", "enrollments", "published"],
		order_by="enrollments desc",
	)


# --------------------------------------------------------------------------- #
#  AI TOOLS  (Claude)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def generate_course_outline(topic, audience="beginners", lessons=8):
	"""Produce a structured course outline for a topic."""
	_guard()
	lessons = max(3, min(int(lessons or 8), 20))
	system = (
		"You are an expert curriculum designer for an online course platform. "
		"Produce a clear, well-structured course outline. Use markdown: a short "
		"course description, learning outcomes, then numbered modules each with "
		"2-4 lessons and a one-line description per lesson. Be concrete and practical."
	)
	user = (
		f"Design a course outline.\nTopic: {topic}\nTarget audience: {audience}\n"
		f"Number of lessons (approx): {lessons}\n"
	)
	text, is_real = _call_claude(system, user, max_tokens=1600, temperature=0.7)
	return {"text": text, "is_real": is_real}


@frappe.whitelist()
def teaching_assistant(question, context=""):
	"""Answer an instructor's question as a teaching/ops assistant."""
	_guard()
	system = (
		"You are the AI teaching assistant for Harshith's learning platform. "
		"You help the course creator with teaching strategy, student engagement, "
		"content ideas, marketing the course, and platform questions. Be concise, "
		"warm, and actionable. Use short paragraphs or bullets."
	)
	user = question if not context else f"Context: {context}\n\nQuestion: {question}"
	text, is_real = _call_claude(system, user, max_tokens=1200, temperature=0.8)
	return {"text": text, "is_real": is_real}


@frappe.whitelist()
def write_lesson(title, course="", level="beginner"):
	"""Draft a full lesson body in markdown."""
	_guard()
	system = (
		"You are an expert instructional writer. Write a complete, engaging lesson "
		"in markdown: a hook intro, clear explanation with examples, a short "
		"'try it yourself' exercise, and a 3-bullet recap. Keep it tight and useful."
	)
	user = f"Write a lesson titled '{title}'."
	if course:
		user += f" It belongs to the course '{course}'."
	user += f" Learner level: {level}."
	text, is_real = _call_claude(system, user, max_tokens=1800, temperature=0.7)
	return {"text": text, "is_real": is_real}


@frappe.whitelist()
def compose_announcement(occasion, tone="friendly"):
	"""Draft a batch/student announcement."""
	_guard()
	system = (
		"You write short, high-converting announcements for an online course "
		"community. Return a punchy subject line and a 2-4 sentence body. Match the "
		"requested tone. No fluff."
	)
	user = f"Write an announcement.\nOccasion: {occasion}\nTone: {tone}"
	text, is_real = _call_claude(system, user, max_tokens=600, temperature=0.85)
	return {"text": text, "is_real": is_real}


@frappe.whitelist()
def student_insights():
	"""
	AI reads the REAL enrollment/progress data and flags at-risk students
	plus growth suggestions. Combines actual stats with a Claude analysis.
	"""
	_guard()

	enrollments = frappe.get_all(
		"LMS Enrollment",
		fields=["member_name", "course", "progress"],
		order_by="progress asc",
		limit=40,
	)
	stalled = [e for e in enrollments if (e.get("progress") or 0) < 25]
	rows = "\n".join(
		f"- {e.get('member_name') or 'student'} | "
		f"{frappe.get_value('LMS Course', e['course'], 'title') or e['course']} | "
		f"{round(e.get('progress') or 0)}% complete"
		for e in enrollments[:25]
	) or "(no enrollments yet)"

	system = (
		"You are a student-success analyst for an online course platform. Given a "
		"list of enrollments with completion %, identify which learners are at risk "
		"of dropping off, spot patterns, and recommend 3-5 concrete retention actions. "
		"Be specific and encouraging. Markdown with short bullets."
	)
	user = f"Enrollment snapshot (lowest progress first):\n{rows}\n\nGive me your read + an action plan."
	text, is_real = _call_claude(system, user, max_tokens=1200, temperature=0.6)
	return {
		"text": text,
		"is_real": is_real,
		"stalled_count": len(stalled),
		"total_reviewed": len(enrollments),
	}
