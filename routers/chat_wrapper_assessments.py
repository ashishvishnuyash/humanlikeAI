"""Full personality and self-efficacy assessment definitions, mirroring the
frontend's ASSESSMENT_DATA in app/api/chat/route.ts. Kept in a separate
module so the chat_wrapper router stays focused on flow logic."""

from __future__ import annotations

ASSESSMENT_DATA: dict = {
    "personality_profiler": {
        "questions": {
            1: "Does your mood fluctuate?",
            2: "Do you bother too much about what others think of you?",
            3: "Do you like talking much?",
            4: "If you make a commitment to someone, do you abide by it irrespective of discomfort?",
            5: "Do you sometimes feel under the weather?",
            6: "If you become broke/bankrupt will it bother you?",
            7: "Are you a happy go lucky person?",
            8: "Do you desire for more than the effort you put in for anything?",
            9: "Do you get anxious easily?",
            10: "Are you curious to try drugs that may be dangerous otherwise?",
            11: "Do you like making new friends?",
            12: "Have you put blame on someone for your own mistake?",
            13: "Are you a sensitive person?",
            14: "Do you prefer having your own way out rather than following a code of conduct?",
            15: "Do you like partying?",
            16: "Are you well behaved and good mannered?",
            17: "Do you often feel offended for no reason?",
            18: "Do you like abiding by rules and remaining neat and clean?",
            19: "Do you like approaching new people?",
            20: "Have you ever stolen anything?",
            21: "Do you get anxious easily?",
            22: "Do you think getting married is futile?",
            23: "Can you bring life to a boring party?",
            24: "Have you ever broken or misplaced something that did not belong to you?",
            25: "Do you overthink and worry a lot?",
            26: "Do you like working in teams?",
            27: "Do you like to take a back seat during social events?",
            28: "Does it keep bothering you if the work you have does is incorrect or has errors?",
            29: "Have you ever backbitten about someone?",
            30: "Are you a high on nerves person?",
            31: "Do you think people expend a lot of time in making future investments?",
            32: "Do you like spending time with people?",
            33: "Were you difficult to handle as a child to your parents?",
            34: "Does an awkward experience keep bothering you even after it is over?",
            35: "Do you try to be polite to people?",
            36: "Do you like a lot of hustle and bustle around you?",
            37: "Have you ever broken rules during any game/sport?",
            38: "Do you suffer from overthinking and nervousness?",
            39: "Do you like to dominate others?",
            40: "Have you ever misused someone's decency?",
            41: "Do you interact less, when with other people?",
            42: "Do you mostly feel alone?",
            43: "Do you prefer following rules set by the society or be a master of you wishes?",
            44: "Are you considered to be an upbeat person by others?",
            45: "Do you follow what you say?",
            46: "Do you often feel embarrassed and guilty?",
            47: "Do you sometimes procrastinate?",
            48: "Can you initiate and bring life to a party?",
        },
        "scoring": {
            "Non-Conformist": {"yes": [10, 14, 22, 31, 39], "no": [2, 6, 18, 26, 28, 35, 43]},
            "Sociable": {"yes": [3, 7, 11, 15, 19, 23, 32, 36, 44, 48], "no": [27, 41]},
            "Emotionally Unstable": {
                "yes": [1, 5, 9, 13, 17, 21, 25, 30, 34, 38, 42, 46], "no": []
            },
            "Socially Desirable": {
                "yes": [4, 16], "no": [8, 12, 20, 24, 29, 33, 37, 40, 45, 47]
            },
        },
        "interpretations": {
            "Sociable": "High scores indicate an outgoing, impulsive, and uninhibited personality. These individuals enjoy social gatherings, have many friends, and prefer excitement and activity.",
            "Unsociable": "Low scores on Sociable dimensions suggest a quiet, retiring, and studious nature. They tend to be reserved, prefer a well-planned life, and keep feelings controlled.",
            "Emotionally Unstable": "High scores indicate strong emotional lability and over-responsiveness. They tend to experience worries and anxieties, especially under stress.",
            "Non-Conformist": "High scores suggest tendencies towards being cruel, inhumane, socially indifferent, hostile, and aggressive. They may lack empathy and act disruptively.",
            "Socially Desirable": "This scale measures the tendency to 'fake good' or provide socially acceptable answers rather than true ones. A high score may indicate the other results are not fully valid.",
        },
        "scoring_instructions": "Please answer 'yes' or 'no' to each question.",
    },
    "self_efficacy_scale": {
        "questions": [
            "I can solve tedious problems with sincere efforts.",
            "If someone disagrees with me, I can still manage to get what I want with ease.",
            "It is easy for me to remain focused on my objectives and achieve my goals.",
            "I have the caliber of dealing efficiently and promptly with obstacles and adversities.",
            "I am resourceful and competent enough to handle unpredictable events and situations.",
            "I can solve problems with ease if I put in requisite effort.",
            "I can remain relaxed even in wake of adversity due to my coping skills.",
            "I can generate alternative solutions with ease even when I come across problematic situations.",
            "If I find myself in a catch twenty two situation, I can still manage finding a solution.",
            "I am mostly capable of handling anything that crosses my path.",
        ],
        "scoring_instructions": "Please rate each statement on a scale of 1 to 4, where 1 is 'Not at all true', 2 is 'Hardly true', 3 is 'Moderately true', and 4 is 'Exactly true'.",
        "interpretation": "The total score will be the sum of your ratings for all 10 items (ranging from 10-40). A higher score indicates higher Self-Efficacy.",
    },
}
