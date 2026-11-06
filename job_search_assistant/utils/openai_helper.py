import os
import json
from dotenv import load_dotenv

load_dotenv()

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.llm import ModelClient


def get_client(task="assistant"):
    return ModelClient(task)


def chat(system_prompt: str, user_prompt: str, temperature: float = 1.0, task: str = "assistant") -> str:
    response = get_client(task).messages.create(
        max_tokens=4096, system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


# ── Resume ──────────────────────────────────────────────────────────────────

def analyze_ats_match(resume_text: str, job_description: str) -> dict:
    system = (
        "You are an expert ATS (Applicant Tracking System) analyst. "
        "Analyze the resume against the job description and return a JSON object with: "
        "score (0-100 integer), missing_keywords (list of strings), "
        "present_keywords (list of strings), suggestions (list of strings). "
        "Return ONLY valid JSON, no markdown fences."
    )
    user = f"RESUME:\n{resume_text}\n\nJOB DESCRIPTION:\n{job_description}"
    raw = chat(system, user)
    try:
        return json.loads(raw)
    except Exception:
        return {
            "score": 0,
            "missing_keywords": [],
            "present_keywords": [],
            "suggestions": ["Could not parse AI response. Please try again."],
        }


def rewrite_resume(resume_text: str, job_description: str,
                   experience_level: str, job_title: str) -> str:
    system = (
        "You are a professional resume writer. Rewrite the provided resume to be "
        "highly tailored for the given job description. Use strong action verbs, "
        "quantify achievements where possible, and embed ATS keywords naturally. "
        f"The candidate is applying for '{job_title}' at {experience_level} level. "
        "Return only the rewritten resume text."
    )
    user = f"RESUME:\n{resume_text}\n\nJOB DESCRIPTION:\n{job_description}"
    return chat(system, user, task="resume")


def improve_bullets(resume_text: str) -> str:
    system = (
        "You are a resume expert. Rewrite every bullet point in this resume to be "
        "more impactful: start with strong action verbs, quantify results, and "
        "highlight impact. Return only the improved resume text."
    )
    return chat(system, resume_text, task="resume")


# ── Cover Letter ─────────────────────────────────────────────────────────────

def generate_cover_letter(resume_text: str, company: str, role: str,
                           hiring_manager: str, job_description: str,
                           tone: str) -> str:
    system = (
        f"You are an expert cover letter writer. Write a {tone.lower()} cover letter "
        f"for the candidate applying to {role} at {company}. "
        f"Address it to {hiring_manager if hiring_manager else 'the Hiring Manager'}. "
        "Make it personalized, compelling, and 3-4 paragraphs. "
        "Return only the cover letter text."
    )
    user = (
        f"RESUME SUMMARY:\n{resume_text[:2000]}\n\n"
        f"JOB DESCRIPTION:\n{job_description}"
    )
    return chat(system, user, task="cover_letter")


# ── Recruiter Email ──────────────────────────────────────────────────────────

def generate_recruiter_email(resume_summary: str, recruiter_name: str,
                              company: str, role: str,
                              linkedin_url: str, tone: str,
                              email_type: str) -> dict:
    system = (
        f"You are an expert at writing {tone.lower()} professional {email_type} "
        f"outreach emails to recruiters. Write a concise, compelling email from the "
        f"candidate to {recruiter_name if recruiter_name else 'the recruiter'} at {company} "
        f"for the {role} position. Return a JSON object with 'subject' and 'body' keys. "
        "Return ONLY valid JSON, no markdown fences."
    )
    user = (
        f"CANDIDATE SUMMARY:\n{resume_summary[:1500]}\n\n"
        f"LinkedIn: {linkedin_url}\nRole: {role}\nCompany: {company}"
    )
    raw = chat(system, user)
    try:
        return json.loads(raw)
    except Exception:
        lines = raw.split("\n", 1)
        return {
            "subject": lines[0].replace("Subject:", "").strip() if lines else "Regarding Opportunity",
            "body": lines[1].strip() if len(lines) > 1 else raw,
        }


# ── Interview Prep ───────────────────────────────────────────────────────────

def generate_interview_questions(role: str, job_description: str,
                                  resume_text: str) -> list:
    system = (
        "You are an expert interview coach. Generate 10 likely interview questions "
        f"for a {role} role based on the job description and resume. "
        "For each question, provide a suggested answer framework. "
        "Return a JSON array of objects with 'question' and 'answer_framework' keys. "
        "Return ONLY valid JSON, no markdown fences."
    )
    user = f"RESUME:\n{resume_text[:1500]}\n\nJOB DESCRIPTION:\n{job_description}"
    raw = chat(system, user)
    try:
        return json.loads(raw)
    except Exception:
        return [{"question": "Could not generate questions.", "answer_framework": "Please try again."}]
