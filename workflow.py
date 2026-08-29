"""Shared rules: subscription expiry, notifications, and the review chain."""

from datetime import date

from flask import current_app, g, url_for

from db import execute, query

# Where work goes next, and who is told about it.
NEXT_STAGE = {
    "with_lecturer": "with_university",
    "with_university": "with_company",
}

STAGE_LABEL = {
    "with_lecturer": "With the lecturer",
    "with_university": "With the university",
    "with_company": "With the company",
    "approved": "Approved",
    "revision": "Correction requested",
}


# ------------------------------------------------------------ subscriptions

def effective_status(status, until):
    """A subscription past its end date is expired, whatever the column says.

    Checked on read rather than by a nightly job, so nothing depends on a
    scheduler being alive.
    """
    if status == "active" and until and until < date.today():
        return "expired"
    return status


def subscription_live(status, until):
    if current_app.config.get("DEMO_MODE"):
        return True
    return effective_status(status, until) == "active"


def expire_lapsed():
    """Write the expiry back so admin lists and reports agree with reality."""
    execute("""UPDATE universities SET subscription_status = 'expired'
                WHERE subscription_status = 'active'
                  AND subscribed_until IS NOT NULL
                  AND subscribed_until < CURRENT_DATE""")
    execute("""UPDATE companies SET subscription_status = 'expired'
                WHERE subscription_status = 'active'
                  AND subscribed_until IS NOT NULL
                  AND subscribed_until < CURRENT_DATE""")


# ------------------------------------------------------------ notifications

def notify(user_id, body, url=None):
    if not user_id:
        return
    execute("INSERT INTO notifications (user_id, body, url) VALUES (%s,%s,%s)",
            (user_id, body, url))


def notify_many(user_ids, body, url=None):
    for uid in {u for u in user_ids if u}:
        notify(uid, body, url)


def unread_count(user_id):
    row = query("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND read_at IS NULL",
                (user_id,), one=True)
    return row["n"] if row else 0


# ------------------------------------------------------------ review chain

def chain_people(assignment_id):
    """The four parties attached to one placement."""
    return query(
        """SELECT asg.student_id, asg.lecturer_id,
                  uni.admin_user_id AS university_id, uni.name AS university_name,
                  co.user_id AS company_user_id, co.name AS company_name,
                  p.id AS project_id, p.title
             FROM assignments asg
             JOIN projects p ON p.id = asg.project_id
             JOIN companies co ON co.id = p.company_id
             LEFT JOIN lecturer_profiles lp ON lp.user_id = asg.lecturer_id
             LEFT JOIN universities uni ON uni.id = lp.university_id
            WHERE asg.id = %s""", (assignment_id,), one=True)


def record_review(submission_id, action, comment, new_stage):
    execute(
        """INSERT INTO reviews (submission_id, reviewer_id, reviewer_role, action, comment)
           VALUES (%s,%s,%s,%s,%s)""",
        (submission_id, g.user["id"], g.user["role"], action, comment or None))
    execute("UPDATE submissions SET stage=%s, updated_at=NOW() WHERE id=%s",
            (new_stage, submission_id))


def submission_for(submission_id):
    return query(
        """SELECT s.*, asg.id AS assignment_id, asg.student_id, asg.lecturer_id,
                  asg.project_id, p.title, p.company_id,
                  stu.fullname AS student_name,
                  lec.fullname AS lecturer_name,
                  lp.university_id,
                  co.user_id AS company_user_id, co.name AS company_name
             FROM submissions s
             JOIN assignments asg ON asg.id = s.assignment_id
             JOIN projects p ON p.id = asg.project_id
             JOIN companies co ON co.id = p.company_id
             JOIN users stu ON stu.id = asg.student_id
             JOIN users lec ON lec.id = asg.lecturer_id
             LEFT JOIN lecturer_profiles lp ON lp.user_id = asg.lecturer_id
            WHERE s.id = %s""", (submission_id,), one=True)


def history(submission_id):
    return query(
        """SELECT r.*, u.fullname FROM reviews r JOIN users u ON u.id = r.reviewer_id
            WHERE r.submission_id = %s ORDER BY r.created_at""", (submission_id,))
