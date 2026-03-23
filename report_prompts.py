

from langchain_core.prompts import ChatPromptTemplate


# ═══════════════════════════════════════════════════════════════════════════
# NODE 1 — analyze_mental_health
# Input: {conversation}
# ═══════════════════════════════════════════════════════════════════════════

ANALYZE_MENTAL_HEALTH = ChatPromptTemplate.from_template(
    "You are a clinical-grade conversational analysis engine.\n\n"
    "Below is a chat conversation between a user and an AI companion.\n"
    "Analyse it carefully and score the user's MENTAL HEALTH across the\n"
    "following 10 dimensions.\n\n"

    "═══ CONVERSATION ═══\n"
    "{conversation}\n\n"

    "═══ SCORING RULES ═══\n"
    "Each metric is scored 0–10:\n"
    "  0–3 = LOW  (significant concern)\n"
    "  3–6 = MEDIUM  (moderate / mixed signals)\n"
    "  6–10 = HIGH  (healthy / positive)\n\n"
    "Exception — 'substance_use': HIGHER score = LESS concern (i.e. 10 = no substance issues).\n\n"

    "═══ METRICS TO SCORE ═══\n"
    "1. emotional_regulation — How well does the user manage emotions?\n"
    "   Look for: emotional outbursts, mood swings, ability to self-soothe,\n"
    "   recovery from setbacks, catastrophising.\n\n"

    "2. stress_anxiety — How stressed / anxious is the user?\n"
    "   Look for: worry, overthinking, physical stress symptoms mentioned,\n"
    "   panic, restlessness, avoidance behaviours.\n\n"

    "3. motivation_engagement — How motivated and engaged are they?\n"
    "   Look for: initiative, goal-setting, enthusiasm, apathy, withdrawal,\n"
    "   hopelessness, giving up, energy levels.\n\n"

    "4. social_connectedness — How socially connected do they feel?\n"
    "   Look for: mentions of friends/family, isolation, loneliness,\n"
    "   conflict with others, desire for connection.\n\n"

    "5. self_esteem — How does the user perceive themselves?\n"
    "   Look for: self-criticism, self-doubt, confidence, comparing to others,\n"
    "   feeling worthless, positive self-talk.\n\n"

    "6. cognitive_functioning — Clarity of thought and decision-making.\n"
    "   Look for: confusion, indecisiveness, rumination, brain fog,\n"
    "   or clear analytical thinking and articulate communication.\n\n"

    "7. emotional_tone — Overall emotional sentiment of their messages.\n"
    "   Look for: positive vs negative language ratio, humour, warmth,\n"
    "   bitterness, hostility, despair.\n\n"

    "8. assertiveness — Ability to express needs and set boundaries.\n"
    "   Look for: people-pleasing, difficulty saying no, standing up for\n"
    "   themselves, clear communication of needs.\n\n"

    "9. work_life_balance — How balanced is their work/personal life?\n"
    "   Look for: overwork, burnout, inability to relax, hobby mentions,\n"
    "   rest, set routines, leisure time.\n\n"

    "10. substance_use — Signs of substance use/abuse.\n"
    "    Look for: mentions of alcohol, drugs, smoking, dependence,\n"
    "    coping through substances.  10 = no substance concerns.\n\n"

    "═══ ADDITIONAL OUTPUTS ═══\n"
    "- trend: Is the user's mental health 'improving', 'stable', or 'declining'\n"
    "  across the conversation? Judge from early vs late messages.\n"
    "- summary: 2-3 sentence narrative summarising their mental health.\n"
    "- confidence: How confident are you in the analysis? (0-1)\n"
    "  Set below 0.4 if the conversation has fewer than ~5 meaningful exchanges,\n"
    "  is off-topic, or contains no health-relevant signals. In these cases,\n"
    "  score all unsupported metrics at 5 (neutral) with weight 0.2.\n\n"

    "═══ WEIGHT GUIDANCE ═══\n"
    "Assign each metric a weight (0-1) reflecting how much EVIDENCE\n"
    "the conversation provides for that metric:\n"
    "  1.0 = explicit, clear evidence\n"
    "  0.5 = moderate indirect signals\n"
    "  0.2 = very little evidence (default to neutral score of 5)\n\n"

    "═══ REASON GUIDANCE ═══\n"
    "For each metric, provide a brief reason (1-2 sentences) citing the specific\n"
    "conversation signal(s) that drove the score. If the score is near-neutral\n"
    "due to lack of evidence, state that explicitly.\n\n"

    "Be precise. Do not inflate scores. Use the full 0-10 range."
)


# ═══════════════════════════════════════════════════════════════════════════
# NODE 2 — analyze_physical_health
# Input: {conversation}
# ═══════════════════════════════════════════════════════════════════════════

ANALYZE_PHYSICAL_HEALTH = ChatPromptTemplate.from_template(
    "You are a clinical-grade conversational analysis engine.\n\n"
    "Below is a chat conversation between a user and an AI companion.\n"
    "Analyse it carefully and score the user's PHYSICAL HEALTH across\n"
    "the following 5 dimensions.\n\n"

    "═══ CONVERSATION ═══\n"
    "{conversation}\n\n"

    "═══ SCORING RULES ═══\n"
    "Each metric is scored 0–10:\n"
    "  0–3 = LOW  (significant concern)\n"
    "  3–6 = MEDIUM  (moderate / mixed signals)\n"
    "  6–10 = HIGH  (healthy / positive)\n\n"

    "═══ METRICS TO SCORE ═══\n"
    "1. activity — Physical activity / exercise level.\n"
    "   Look for: exercise mentions, sedentary behaviour, energy for\n"
    "   physical tasks, sports, walking, gym references.\n\n"

    "2. nutrition — Eating habits and diet quality.\n"
    "   Look for: meal mentions, skipping meals, binge eating, healthy\n"
    "   vs junk food references, hydration, appetite changes.\n\n"

    "3. pain — Physical pain or discomfort.\n"
    "   Look for: headaches, body aches, chronic pain, fatigue,\n"
    "   sleep problems, physical complaints.\n"
    "   NOTE: HIGHER score = LESS pain (10 = no pain concerns).\n\n"

    "4. lifestyle — General lifestyle quality.\n"
    "   Look for: sleep schedule, hygiene, routine, rest,\n"
    "   screen time, outdoor activity, hobbies.\n\n"

    "5. absenteeism — Missing work/school/commitments due to health.\n"
    "   Look for: calling in sick, missing events, cancelling plans,\n"
    "   being unable to function, staying in bed.\n"
    "   NOTE: HIGHER score = LESS absenteeism (10 = no absences).\n\n"

    "═══ ADDITIONAL OUTPUTS ═══\n"
    "- trend: 'improving', 'stable', or 'declining'\n"
    "- summary: 2-3 sentence narrative of physical health.\n"
    "- confidence: 0-1, reflecting evidence available.\n"
    "  Set below 0.4 if the conversation has fewer than ~5 meaningful exchanges\n"
    "  or contains no health-relevant signals.\n\n"

    "═══ WEIGHT GUIDANCE ═══\n"
    "Assign each metric a weight (0-1) reflecting how much EVIDENCE\n"
    "the conversation provides.  Physical health is often under-discussed\n"
    "in chat, so set low weights when evidence is thin and default\n"
    "to neutral (5) scores with low weights.\n"
    "This applies equally to inverted metrics (pain, absenteeism): absence of\n"
    "complaints does NOT mean a score of 10 — use 5 with low weight unless\n"
    "the user explicitly reports no pain or no absences.\n\n"

    "═══ REASON GUIDANCE ═══\n"
    "For each metric, provide a brief reason (1-2 sentences) citing the specific\n"
    "conversation signal(s) that drove the score. If the score is near-neutral\n"
    "due to lack of evidence, state that explicitly.\n\n"

    "Be precise. Do not inflate scores."
)


# ═══════════════════════════════════════════════════════════════════════════
# NODE 3 — generate_overall
# Input: {conversation}, {mental_health_summary}, {physical_health_summary}
# ═══════════════════════════════════════════════════════════════════════════

GENERATE_OVERALL = ChatPromptTemplate.from_template(
    "You are a senior wellness analyst synthesising a final health report.\n\n"
    "You have two analysis summaries from independent specialist agents below.\n"
    "Each agent analysed the conversation in isolation — neither was aware of\n"
    "the other's findings. Your primary job is to synthesise both and identify\n"
    "cross-domain connections (e.g., physical pain worsening mental state,\n"
    "anxiety manifesting as physical symptoms) that neither agent would have\n"
    "noted independently. Use the original conversation to verify and enrich\n"
    "both summaries.\n\n"

    "═══ CONVERSATION ═══\n"
    "{conversation}\n\n"

    "═══ MENTAL HEALTH ANALYSIS ═══\n"
    "{mental_health_summary}\n\n"

    "═══ PHYSICAL HEALTH ANALYSIS ═══\n"
    "{physical_health_summary}\n\n"

    "═══ YOUR TASK ═══\n"
    "1. score (0-10): Overall health score combining mental + physical.\n"
    "   Use a weighted average: mental health contributes 60%, physical 40%\n"
    "   (mental signals are more directly observable in conversation).\n"
    "   Down-weight a domain if its confidence is significantly lower than\n"
    "   the other's. You may adjust by ±1 point if one domain is dramatically\n"
    "   worse than the other.\n"
    "2. confidence (0-1): Your confidence in this overall assessment.\n"
    "3. trend: 'improving', 'stable', or 'declining'.\n"
    "4. priority: 'low' (score >= 7.0), 'medium' (4.0 <= score < 7.0), 'high' (score < 4.0).\n"
    "   At the exact boundary: score 7.0 → 'low', score 4.0 → 'medium'.\n"
    "5. summary: 1-2 sentence overall summary.\n"
    "6. full_report: Detailed paragraph (150-300 words) covering both mental\n"
    "   and physical health, specific observations, and cross-domain connections.\n"
    "7. key_insights: 3-5 bullet-point insights about the user's wellbeing.\n"
    "   Each must be grounded in a specific observation from the conversation —\n"
    "   do not include insights that could apply to any person.\n"
    "8. strengths: 2-4 positive attributes / protective factors observed.\n"
    "   Only include strengths evidenced by something the user said or did.\n"
    "9. risks: 2-4 risk areas that need attention. Only flag risks supported\n"
    "   by concrete signals; do not infer risks from silence or absence of topics.\n"
    "   Calibrate to the confidence scores from the specialist agents.\n"
    "10. recommendations: 3-5 specific, actionable recommendations tailored to\n"
    "    this user's situation. Avoid generic advice (e.g., 'exercise regularly')\n"
    "    unless the conversation specifically shows a gap in that area.\n\n"

    "Keep the language compassionate but clinical. Do not diagnose.\n"
    "Focus on observable patterns from the conversation."
)
