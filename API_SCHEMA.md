# Diltak API — Complete Input / Output Schema Reference

> **Base URL**: `http://localhost:8000/api`  
> **Authentication**: All protected endpoints require `Authorization: Bearer <firebase_id_token>`  
> **Content-Type**: `application/json`

---

## Quick Reference

| Group | Prefix | Auth Required |
|---|---|---|
| Auth | `/api/auth/` | Public (register/login), JWT (others) |
| Employees | `/api/employees/` | JWT + Employer/HR role |
| **Employer CRUD** | `/api/employer/` (profile/company) | JWT + Employer/HR role |
| Team Dashboard | `/api/employer/` (analytics) | JWT + Employer/HR role |
| Org Analytics | `/api/employer/org/` | JWT + Employer/HR role |
| Advanced Insights | `/api/employer/insights/` | JWT + Employer/HR role |
| Action Engine | `/api/employer/actions/` | JWT + Employer/HR role |
| **Super Admin** | `/api/admin/` | JWT + super_admin role |
| Chat | `/api/chat/` | JWT |
| Recommendations | `/api/recommendations/` | JWT |
| Reports | `/api/reports/` | JWT |

---

## 1. AUTH

### `POST /api/auth/register` — ~~Employer Registration~~ **[DISABLED]**
> ⚠️ **Disabled** — Always returns `403`. Use `POST /api/admin/employers` instead.

#### Response `403 Forbidden`
```json
{
  "detail": "Self-registration is disabled. Employer accounts must be created by the Diltak admin. Contact admin@diltak.ai or use POST /api/admin/employers."
}
```

---

### `POST /api/auth/login` — Login (Any User)
> **Public** — No token needed

#### Input
```json
{
  "email":    "ayesha@corp.com",  // required
  "password": "Secure@123"        // required
}
```

#### Output `200 OK`
```json
{
  "message":      "Login successful!",
  "access_token": "eyJhbGci...",   // Firebase ID token — use as Bearer token
  "token_type":   "bearer",
  "expires_in":   "3600",
  "user": {
    "uid":         "firebase_uid",
    "email":       "ayesha@corp.com",
    "displayName": "Ayesha Khan",
    "role":        "employer",           // employer | manager | employee | hr
    "companyId":   "company_abc123",
    "companyName": "TechCorp Pvt Ltd"
  }
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 401 | Invalid email or password |
| 500 | Firebase API key missing |

---

### `GET /api/auth/me` — Current User (JWT Payload + Profile)
> **Protected** — Any valid JWT

#### Input — None (token in header)

#### Output `200 OK`
```json
{
  "message": "Authenticated",
  "token_payload": {
    "uid":           "firebase_uid",
    "email":         "ayesha@corp.com",
    "email_verified": true,
    "iat":           1712300000,
    "exp":           1712303600
  },
  "database_profile": {
    "id":            "firebase_uid",
    "email":         "ayesha@corp.com",
    "first_name":    "Ayesha",
    "last_name":     "Khan",
    "role":          "employer",
    "company_id":    "company_abc123",
    "company_name":  "TechCorp Pvt Ltd",
    "is_active":     true,
    "can_view_team_reports": true,
    "can_manage_employees":  true,
    "hierarchy_level":       0
  }
}
```

---

### `GET /api/auth/profile` — Full Structured Profile
> **Protected** — Any valid JWT

#### Input — None (token in header)

#### Output `200 OK`
```json
{
  "uid":          "firebase_uid",
  "email":        "ayesha@corp.com",
  "firstName":    "Ayesha",
  "lastName":     "Khan",
  "role":         "employer",
  "companyId":    "company_abc123",
  "companyName":  "TechCorp Pvt Ltd",
  "industry":     null,
  "companySize":  null,
  "jobTitle":     "CEO",
  "phone":        "+923001234567",
  "isActive":     true,
  "permissions": {
    "can_view_team_reports": true,
    "can_manage_employees":  true,
    "can_approve_leaves":    true,
    "can_view_analytics":    true,
    "can_create_programs":   true,
    "skip_level_access":     true
  },
  "createdAt": "2025-01-01T00:00:00+00:00"
}
```

---

## 2. EMPLOYEES

> **All endpoints require**: `Authorization: Bearer <token>` where token belongs to a user with `role: employer | hr`

### `POST /api/employees/create` — Create Employee
> **Employer / HR only**

#### Input
```json
{
  "email":          "ali@corp.com",    // required, unique email
  "password":       "Pass123",         // required, min 6 chars
  "firstName":      "Ali",             // required
  "lastName":       "Ahmed",           // required
  "role":           "employee",        // required: employee | manager | hr
  "department":     "Engineering",     // optional
  "position":       "Senior Dev",      // optional
  "phone":          "+923009876543",   // optional
  "managerId":      "mgr_uid_xyz",     // optional — must be in same company
  "hierarchyLevel": 2,                 // optional, default 1
  "permissions": {                     // optional — overrides role defaults
    "can_view_team_reports": false,
    "can_approve_leaves":    false
  },
  "sendWelcomeEmail": true             // optional, default true (reserved for future)
}
```

> **Role default permissions applied automatically:**
> | Role | Reports | Manage Emp | Approve Leave | Analytics | Programs |
> |---|---|---|---|---|---|
> | employee | ❌ | ❌ | ❌ | ❌ | ❌ |
> | manager  | ✅ | ❌ | ✅ | ✅ | ❌ |
> | hr       | ✅ | ✅ | ✅ | ✅ | ✅ |

#### Output `201 Created`
```json
{
  "success": true,
  "uid":     "new_employee_firebase_uid",
  "message": "Employee 'Ali Ahmed' created successfully."
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 400 | Password < 6 chars, invalid role, or manager not in same company |
| 403 | Caller is not employer/hr |
| 409 | Email already exists |
| 500 | Firestore write failed (Firebase Auth account auto-rolled back) |

---

### `GET /api/employees` — List All Employees
> **Employer / HR only**

#### Query Parameters
| Param | Type | Default | Description |
|---|---|---|---|
| `include_inactive` | bool | `false` | Include deactivated accounts |
| `department` | string | `null` | Filter by department name |
| `role` | string | `null` | Filter by role: employee / manager / hr |

#### Example
```
GET /api/employees?department=Engineering&role=employee&include_inactive=false
```

#### Output `200 OK`
```json
{
  "success":   true,
  "total":     12,
  "companyId": "company_abc123",
  "employees": [
    {
      "uid":            "emp_uid_1",
      "email":          "ali@corp.com",
      "firstName":      "Ali",
      "lastName":       "Ahmed",
      "role":           "employee",
      "department":     "Engineering",
      "position":       "Senior Dev",
      "phone":          "+923009876543",
      "companyId":      "company_abc123",
      "managerId":      "mgr_uid_xyz",
      "hierarchyLevel": 2,
      "isActive":       true,
      "permissions": {
        "can_view_team_reports": false,
        "can_manage_employees":  false,
        "can_approve_leaves":    false,
        "can_view_analytics":    false,
        "can_create_programs":   false,
        "skip_level_access":     false
      },
      "createdAt":  "2025-01-15T10:00:00+00:00",
      "createdBy":  "employer_uid_abc"
    }
  ]
}
```

---

### `GET /api/employees/{uid}` — Get Single Employee
> **Employer / HR only**

#### Path Parameter
- `uid` — Firebase UID of the employee

#### Output `200 OK` — Same as single item in `employees[]` list above

#### Error Codes
| Status | Condition |
|---|---|
| 403 | Employee belongs to a different company |
| 404 | Employee not found |

---

### `PATCH /api/employees/{uid}` — Update Employee
> **Employer / HR only** — Only send fields you want to change

#### Input (all optional)
```json
{
  "firstName":      "Ali",
  "lastName":       "Raza",
  "department":     "Product",
  "position":       "Product Manager",
  "phone":          "+923001111111",
  "managerId":      "new_mgr_uid",    // "none" to remove manager
  "hierarchyLevel": 3,
  "role":           "manager",
  "permissions": {
    "can_approve_leaves": true
  }
}
```

#### Output `200 OK`
```json
{
  "success":       true,
  "uid":           "emp_uid_1",
  "message":       "Employee updated successfully.",
  "updatedFields": ["last_name", "department", "position"]
}
```

---

### `POST /api/employees/{uid}/deactivate` — Deactivate Employee
> **Employer / HR only**

#### Input — None (uid in path)

#### Output `200 OK`
```json
{
  "success": true,
  "uid":     "emp_uid_1",
  "message": "Employee deactivated successfully."
}
```

---

### `POST /api/employees/{uid}/reactivate` — Reactivate Employee
> **Employer / HR only**

#### Output `200 OK`
```json
{
  "success": true,
  "uid":     "emp_uid_1",
  "message": "Employee reactivated successfully."
}
```

---

## 3. TEAM DASHBOARD (Manager-Level)

> **All require**: Employer/HR JWT + `?company_id=<company_id>`

---

### `GET /api/employer/wellness-index` — Team Wellness Index

#### Query Parameters
| Param | Type | Default | Range |
|---|---|---|---|
| `company_id` | string | **required** | — |
| `period_days` | int | `30` | 7–90 |

#### Output `200 OK`
```json
{
  "company_id":                  "company_abc123",
  "team_size_band":              "25–50",
  "wellness_index":              67.4,
  "stress_score":                71.2,
  "engagement_score":            58.0,
  "check_in_participation_pct":  58.0,
  "period_days":                 30,
  "trend_vs_prior_period":       3.2,
  "data_quality":                "high",
  "computed_at":                 "2025-04-05T04:00:00+00:00"
}
```

> **`wellness_index`** is 0–100. Higher = healthier team.  
> **`trend_vs_prior_period`** is +/- delta versus the previous same window. `null` if insufficient prior data.  
> **`data_quality`**: `high` | `medium` | `low`

---

### `GET /api/employer/burnout-trend` — Burnout Risk Trend

#### Query Parameters
| Param | Type | Default | Range |
|---|---|---|---|
| `company_id` | string | **required** | — |
| `weeks` | int | `8` | 2–12 |

#### Output `200 OK`
```json
{
  "company_id":   "company_abc123",
  "period_weeks": 8,
  "alert_level":  "amber",
  "buckets": [
    { "label": "low",    "percentage": 55.0, "trend": "stable" },
    { "label": "medium", "percentage": 30.0, "trend": "stable" },
    { "label": "high",   "percentage": 15.0, "trend": "rising" }
  ],
  "weekly_distribution": [
    {
      "week":       "W14 2025",
      "low_pct":    60.0,
      "medium_pct": 25.0,
      "high_pct":   15.0,
      "sample_quality": "sufficient"
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

> **`alert_level`**: `green` (< 15% high), `amber` (15–30%), `red` (≥ 30%)  
> **`trend`**: `rising` | `falling` | `stable`

---

### `GET /api/employer/engagement-signals` — Engagement Signals

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `period_days` | int | `30` |

#### Output `200 OK`
```json
{
  "company_id":                "company_abc123",
  "dau_pct":                   24.5,
  "wau_pct":                   61.2,
  "check_in_completion_pct":   58.0,
  "avg_session_depth_score":   6.4,
  "period_days":               30,
  "computed_at":               "2025-04-05T04:00:00+00:00"
}
```

> **`dau_pct`** — % of team who used Diltak today  
> **`wau_pct`** — % of team who used Diltak this week  
> **`avg_session_depth_score`** — 0–10, proxy for session engagement depth

---

### `GET /api/employer/workload-friction` — Workload Friction

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `period_days` | int | `30` |

#### Output `200 OK`
```json
{
  "company_id":               "company_abc123",
  "late_night_activity_pct":  22.5,
  "sentiment_shift_events":   15,
  "overload_pattern_score":   5.8,
  "risk_level":               "medium",
  "period_days":              30,
  "computed_at":              "2025-04-05T04:00:00+00:00"
}
```

> **`late_night_activity_pct`** — % of sessions between 21:00–02:00  
> **`sentiment_shift_events`** — bucketed to nearest 5 for privacy  
> **`risk_level`**: `low` | `medium` | `high`

---

### `GET /api/employer/productivity-proxy` — Productivity Proxy

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `weeks` | int | `8` |

#### Output `200 OK`
```json
{
  "company_id":       "company_abc123",
  "engagement_trend": [48.2, 52.0, 55.5, 61.0, 58.3, 63.1, 67.4, 65.0],
  "period_label":     ["W08 2025", "W09 2025", "W10 2025", "W11 2025", "W12 2025", "W13 2025", "W14 2025", "W15 2025"],
  "correlation_note": "Engagement trend correlates with productivity proxies when optional integrations are connected.",
  "data_quality":     "high",
  "computed_at":      "2025-04-05T04:00:00+00:00"
}
```

---

### `GET /api/employer/early-warnings` — Early Warning Alerts

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `period_days` | int | `14` |

#### Output `200 OK`
```json
{
  "company_id":   "company_abc123",
  "overall_risk": "amber",
  "alerts": [
    {
      "signal":      "stress_rising",
      "description": "Team stress has risen meaningfully over the last 14 days.",
      "confidence":  "high",
      "period":      "last 14 days",
      "attribution": "none"
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

> **Possible signals**: `stress_rising` | `mood_declining` | `engagement_drop`  
> **`confidence`**: `low` | `medium` | `high`  
> **`attribution`** is always `"none"` — privacy guarantee, no individual named

---

### `GET /api/employer/suggested-actions` — Suggested Actions Playbook

#### Query Parameters
| Param | Type |
|---|---|
| `company_id` | **required** |

#### Output `200 OK`
```json
{
  "company_id":    "company_abc123",
  "generated_at":  "2025-04-05T04:00:00+00:00",
  "actions": [
    {
      "trigger":          "stress_rising",
      "category":         "workload",
      "action":           "Initiate a team workload rebalance conversation",
      "expected_impact":  "15–25% stress reduction within 2 weeks if sustained",
      "playbook_steps": [
        "Schedule a team-level workload review (async-friendly format).",
        "Identify and defer non-urgent deliverables for 1–2 sprints.",
        "..."
      ],
      "priority": "high"
    }
  ]
}
```

> **`category`**: `workload` | `engagement` | `schedule` | `manager`  
> **`priority`**: `high` | `medium` | `low`

---

## 4. ORG ANALYTICS (HR-Level)

### `GET /api/employer/org/wellness-trend` — Org Wellness Trend

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `weeks` | int | `12` |

#### Output `200 OK`
```json
{
  "company_id":    "company_abc123",
  "period_weeks":  12,
  "overall_index": 65.2,
  "direction":     "improving",
  "trend": [
    {
      "week":             "W04 2025",
      "wellness_index":   62.1,
      "sample_size_band": "25–50"
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

> **`direction`**: `improving` | `declining` | `stable`

---

### `GET /api/employer/org/department-comparison` — Department Comparison

#### Query Parameters
| Param | Type | Default | Notes |
|---|---|---|---|
| `company_id` | string | **required** | — |
| `period_days` | int | `30` | — |
| `mask_labels` | bool | `true` | `true` = A/B/C labels, `false` = real dept names |

#### Output `200 OK`
```json
{
  "company_id":    "company_abc123",
  "label_masking": true,
  "period_days":   30,
  "hotspot_label": "B",
  "departments": [
    {
      "label":           "A",
      "wellness_index":  72.3,
      "burnout_risk":    "low",
      "engagement_pct":  68.0,
      "size_band":       "10–25",
      "suppressed":      false
    },
    {
      "label":     "B",
      "wellness_index": 0,
      "burnout_risk":   "unknown",
      "engagement_pct": 0,
      "size_band":      "<5 (suppressed)",
      "suppressed":     true
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

> **`suppressed: true`** — department has < 5 members; no data shown (privacy)

---

### `GET /api/employer/org/retention-risk` — Retention Risk Signal

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `period_days` | int | `60` |

#### Output `200 OK`
```json
{
  "company_id":  "company_abc123",
  "period_days": 60,
  "overall_risk": "amber",
  "note": "Modelled from engagement + stress proxy signals. No individual data.",
  "risk_bands": [
    { "band": "low",    "percentage": 65.0, "trend": "stable" },
    { "band": "medium", "percentage": 22.0, "trend": "stable" },
    { "band": "high",   "percentage": 13.0, "trend": "rising" }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

---

### `GET /api/employer/org/diltak-engagement` — Diltak Engagement

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `period_days` | int | `30` |

#### Output `200 OK`
```json
{
  "company_id":                    "company_abc123",
  "adoption_pct":                  72.5,
  "wau_pct":                       55.0,
  "voice_sessions_pct":            35.0,
  "text_sessions_pct":             65.0,
  "completion_rate_pct":           81.2,
  "avg_sessions_per_active_user":  4.2,
  "period_days":                   30,
  "computed_at":                   "2025-04-05T04:00:00+00:00"
}
```

---

### `GET /api/employer/org/roi-impact` — ROI / Impact

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `weeks` | int | `8` |

#### Output `200 OK`
```json
{
  "company_id":  "company_abc123",
  "data_quality": "high",
  "summary": "Higher team wellbeing correlates with increased Diltak engagement.",
  "correlations": [
    {
      "period":                  "W08 2025",
      "wellbeing_index":         63.5,
      "proxy_metric":            "diltak_engagement_pct",
      "proxy_value":             52.0,
      "correlation_direction":   "positive"
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

> **`correlation_direction`**: `positive` | `negative` | `neutral`

---

### `GET /api/employer/org/program-effectiveness` — Program Effectiveness

#### Query Parameters
| Param | Type |
|---|---|
| `company_id` | **required** |

#### Output `200 OK`
```json
{
  "company_id":     "company_abc123",
  "overall_lift":   4.2,
  "recommendation": "Interventions show positive impact. Continue and scale successful programs.",
  "cohorts": [
    {
      "label":        "Stress Resilience Sprint",
      "before_index": 58.0,
      "after_index":  62.5,
      "delta":        4.5,
      "size_band":    "10–25",
      "suppressed":   false
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

---

## 5. ADVANCED INSIGHTS

### `GET /api/employer/insights/predictive-trends` — Predictive Trends

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `forecast_weeks` | int | `4` | 1–8 |

#### Output `200 OK`
```json
{
  "company_id":     "company_abc123",
  "forecast_weeks": 4,
  "model_note":     "Burnout risk uses a stress + inverse-mood proxy model. Forecast uses linear extrapolation.",
  "historical": [
    {
      "week":               "W10 2025",
      "burnout_risk_pct":   18.5,
      "attrition_risk_pct": 11.1,
      "confidence":         "high"
    }
  ],
  "forecast": [
    {
      "week":               "[forecast] W16 2025",
      "burnout_risk_pct":   20.2,
      "attrition_risk_pct": 12.1,
      "confidence":         "medium"
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

---

### `GET /api/employer/insights/benchmarks` — Benchmarks

#### Query Parameters
| Param | Type | Default | Notes |
|---|---|---|---|
| `company_id` | string | **required** | — |
| `period_days` | int | `30` | — |
| `industry` | string | `null` | tech / finance / healthcare / retail / education |

#### Output `200 OK`
```json
{
  "company_id":  "company_abc123",
  "industry":    "tech",
  "summary":     "Your team is above benchmark on 2 of 3 metrics.",
  "period_days": 30,
  "comparisons": [
    {
      "metric":           "wellness_index",
      "your_value":       67.4,
      "benchmark_value":  62.0,
      "delta":            5.4,
      "direction":        "above",
      "benchmark_source": "Anonymised tech median (Diltak network)"
    },
    {
      "metric":           "burnout_high_pct",
      "your_value":       22.0,
      "benchmark_value":  18.0,
      "delta":            4.0,
      "direction":        "below",
      "benchmark_source": "Anonymised tech median (Diltak network)"
    },
      "metric":           "diltak_engagement_pct",
      "your_value":       61.0,
      "benchmark_value":  55.0,
      "delta":            6.0,
      "direction":        "above",
      "benchmark_source": "Anonymised tech median (Diltak network)"
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

> **`direction`**: `above` | `below` | `at_par`

---

### `GET /api/employer/insights/cohorts` — Tenure Cohort Analysis

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `company_id` | string | **required** |
| `period_days` | int | `30` |

#### Output `200 OK`
```json
{
  "company_id":   "company_abc123",
  "period_days":  30,
  "privacy_note": "Data aggregated by tenure band. Individual user data is never exposed.",
  "cohorts": [
    {
      "label":           "0–6 months",
      "size_band":       "5–10",
      "wellness_index":  58.2,
      "burnout_risk":    "medium",
      "engagement_pct":  51.0,
      "suppressed":      false
    },
    {
      "label":       "6–12 months",
      "size_band":   "<5 (suppressed)",
      "wellness_index": 0,
      "burnout_risk": "unknown",
      "engagement_pct": 0,
      "suppressed":  true
    },
    {
      "label":           "1–3 years",
      "size_band":       "25–50",
      "wellness_index":  70.1,
      "burnout_risk":    "low",
      "engagement_pct":  72.0,
      "suppressed":      false
    },
    {
      "label":           "3+ years",
      "size_band":       "10–25",
      "wellness_index":  66.8,
      "burnout_risk":    "low",
      "engagement_pct":  65.0,
      "suppressed":      false
    }
  ],
  "computed_at": "2025-04-05T04:00:00+00:00"
}
```

---

## 6. ACTION ENGINE

### `POST /api/employer/actions/manager-playbook` — Manager Playbook

#### Input
```json
{
  "company_id": "company_abc123",
  "signal":     "stress_rising"
}
```

> **Valid signals**: `stress_rising` | `engagement_drop` | `mood_declining` | `late_night_spikes` | `burnout_high`

#### Output `200 OK`
```json
{
  "company_id":       "company_abc123",
  "signal":           "stress_rising",
  "insight":          "Team stress levels have risen meaningfully. This is a leading indicator of potential burnout.",
  "recommendation":   "Initiate a structured workload rebalance and increase psychological safety initiatives.",
  "expected_impact":  "15–25% stress reduction within 2–3 weeks if sustained.",
  "confidence":       "high",
  "steps": [
    {
      "step":             "Conduct an async team workload review — ask team to flag what feels overloaded.",
      "owner":            "Manager",
      "timeline":         "immediate",
      "expected_outcome": "Shared visibility on workload hotspots."
    },
    {
      "step":             "Defer non-critical deliverables by 1–2 sprints.",
      "owner":            "Manager",
      "timeline":         "this_week",
      "expected_outcome": "Immediate pressure reduction."
    }
  ],
  "guardrails": [
    "Do not discuss individual stress levels with team members — address patterns only.",
    "Escalate to HR if stress signals persist beyond 3 weeks."
  ],
  "generated_at": "2025-04-05T04:00:00+00:00"
}
```

> **`timeline`**: `immediate` | `this_week` | `this_month`  
> **`owner`**: `Manager` | `HR` | `Employee`

#### Error Codes
| Status | Condition |
|---|---|
| 400 | Unknown signal value |
| 403 | Not an employer/hr account |

---

### `POST /api/employer/actions/hr-playbook` — HR Playbook

#### Input
```json
{
  "company_id":        "company_abc123",
  "signals":           ["stress_rising", "late_night_spikes"],
  "department_label":  "B"
}
```

> **`signals`** — array of active signal strings (from Early Warnings or your own assessment)  
> **`department_label`** — optional anonymous dept label (A/B/C from department comparison)

#### Output `200 OK`
```json
{
  "company_id":     "company_abc123",
  "active_signals": ["stress_rising", "late_night_spikes"],
  "format_note":    "Format: Insight → Recommendation → Expected Impact → Playbook. All programs generic to team.",
  "programs": [
    {
      "program_name":    "Stress Resilience Sprint",
      "target_signal":   "stress_rising",
      "delivery":        "digital",
      "duration_weeks":  4,
      "expected_lift":   "15–25% stress reduction",
      "priority":        "immediate"
    },
    {
      "program_name":    "Sleep & Schedule Hygiene Track",
      "target_signal":   "late_night_spikes",
      "delivery":        "digital",
      "duration_weeks":  4,
      "expected_lift":   "Reduced after-hours activity + 10% sleep score improvement",
      "priority":        "next_cycle"
    }
  ],
  "policy_adjustments": [
    "Review and update right-to-disconnect policy.",
    "Consider flexible work arrangements (FlexTime / compressed weeks).",
    "Audit PTO culture — ensure team is taking allocated leave."
  ],
  "manager_enablement": [
    "Activate: 'Recognising Burnout Early' manager training module.",
    "Activate: 'Leading with Async-First Practices' module."
  ],
  "generated_at": "2025-04-05T04:00:00+00:00"
}
```

> **`delivery`**: `digital` | `async` | `live`  
> **`priority`**: `immediate` | `next_cycle` | `optional`

---

## Global Error Schema

All errors return this shape:
```json
{
  "detail": "Human-readable error message"
}
```

Or for validation errors (422):
```json
{
  "detail": [
    {
      "loc":  ["body", "email"],
      "msg":  "value is not a valid email address",
      "type": "value_error.email"
    }
  ]
}
```

Privacy suppression (422):
```json
{
  "detail": {
    "error":     "insufficient_cohort",
    "message":   "Team size or data volume too small to compute anonymised metrics.",
    "suppressed": true
  }
}
```

---

## Firestore Collections Used

| Collection | Purpose | Key Fields |
|---|---|---|
| `users` | All user profiles | `id`, `email`, `role`, `company_id`, `department`, `manager_id`, `is_active` |
| `companies` | Company metadata | `id`, `name`, `industry`, `size`, `owner_id`, `employee_count` |
| `check_ins` | Daily wellness check-ins | `user_id`, `company_id`, `mood_score` (1–10), `stress_level` (1–10), `created_at` |
| `sessions` | Diltak session logs | `user_id`, `company_id`, `modality` (`voice`/`text`), `completed` (bool), `depth_score`, `created_at` |
| `wellness_events` | Flagged signals | `user_id`, `company_id`, `event_type` (e.g. `sentiment_negative_shift`), `created_at` |
| `interventions` | HR programs | `company_id`, `label`, `start_date`, `end_date` |
| `chat_sessions` | AI chat sessions | `user_id`, `company_id`, `messages[]`, `created_at` |
| `mental_health_reports` | Wellness reports | `employee_id`, `company_id`, `overall_wellness`, `risk_level`, `created_at` |

---

## TypeScript Integration

```typescript
// types/api.ts — paste into your frontend types folder

export interface RegisterRequest {
  firstName:    string;
  lastName:     string;
  email:        string;
  password:     string;        // min 8 chars
  companyName:  string;        // min 2 chars
  companySize?: string;
  industry?:    string;
  jobTitle?:    string;
  phone?:       string;
}

export interface RegisterResponse {
  message:   string;
  userId:    string;
  companyId: string;
  role:      'employer';
}

export interface LoginRequest {
  email:    string;
  password: string;
}

export interface LoginResponse {
  message:      string;
  access_token: string;          // use as: `Authorization: Bearer ${access_token}`
  token_type:   'bearer';
  expires_in:   string;
  user: {
    uid:         string;
    email:       string;
    displayName: string;
    role:        'employer' | 'manager' | 'employee' | 'hr';
    companyId:   string | null;
    companyName: string | null;
  };
}

export interface CreateEmployeeRequest {
  email:           string;
  password:        string;       // min 6 chars
  firstName:       string;
  lastName:        string;
  role:            'employee' | 'manager' | 'hr';
  department?:     string;
  position?:       string;
  phone?:          string;
  managerId?:      string;
  hierarchyLevel?: number;
  permissions?:    Record<string, boolean>;
  sendWelcomeEmail?: boolean;
}

export interface EmployeeProfile {
  uid:            string;
  email:          string;
  firstName:      string;
  lastName:       string;
  role:           string;
  department:     string | null;
  position:       string | null;
  phone:          string | null;
  companyId:      string;
  managerId:      string | null;
  hierarchyLevel: number;
  isActive:       boolean;
  permissions: {
    can_view_team_reports: boolean;
    can_manage_employees:  boolean;
    can_approve_leaves:    boolean;
    can_view_analytics:    boolean;
    can_create_programs:   boolean;
    skip_level_access:     boolean;
  };
  createdAt:  string | null;
  createdBy:  string | null;
}

export type RiskLevel    = 'low' | 'medium' | 'high';
export type AlertLevel   = 'green' | 'amber' | 'red';
export type TrendDir     = 'rising' | 'falling' | 'stable';
export type DataQuality  = 'high' | 'medium' | 'low' | 'insufficient';
export type Confidence   = 'low' | 'medium' | 'high';
export type Direction    = 'above' | 'below' | 'at_par';
export type BenchmarkDir = 'improving' | 'declining' | 'stable';

export interface WellnessIndexResponse {
  company_id:                  string;
  team_size_band:              string;
  wellness_index:              number;    // 0–100
  stress_score:                number;
  engagement_score:            number;
  check_in_participation_pct:  number;
  period_days:                 number;
  trend_vs_prior_period:       number | null;
  data_quality:                DataQuality;
  computed_at:                 string;
}

export interface EarlyWarningAlert {
  signal:      string;
  description: string;
  confidence:  Confidence;
  period:      string;
  attribution: 'none';    // always none — never individual
}

export interface ManagerPlaybookRequest {
  company_id: string;
  signal:     'stress_rising' | 'engagement_drop' | 'mood_declining' | 'late_night_spikes' | 'burnout_high';
}

export interface HRPlaybookRequest {
  company_id:        string;
  signals:           string[];
  department_label?: string;
}
```

---

## Usage Examples

```typescript
// Register employer
const res = await fetch('/api/auth/register', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    firstName: 'Ayesha', lastName: 'Khan',
    email: 'ayesha@corp.com', password: 'Secure@123',
    companyName: 'TechCorp', industry: 'Tech'
  })
});

// Login and store token
const { access_token, user } = await res.json();
localStorage.setItem('token', access_token);

// Create employee (employer only)
const token = localStorage.getItem('token');
await fetch('/api/employees/create', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  },
  body: JSON.stringify({
    email: 'ali@corp.com', password: 'Pass123',
    firstName: 'Ali', lastName: 'Ahmed',
    role: 'employee', department: 'Engineering'
  })
});

// Get wellness index
const data = await fetch(
  `/api/employer/wellness-index?company_id=${user.companyId}&period_days=30`,
  { headers: { 'Authorization': `Bearer ${token}` } }
).then(r => r.json());

// Get manager playbook
await fetch('/api/employer/actions/manager-playbook', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  },
  body: JSON.stringify({ company_id: user.companyId, signal: 'stress_rising' })
});
```

---

## 7. EMPLOYER CRUD

> **All endpoints require**: `Authorization: Bearer <token>` where token belongs to `role: employer | hr`
> `PATCH /profile`, `PATCH /company`, `DELETE /account`, `POST /change-password` → **employer only** (owner)

---

### `GET /api/employer/profile` — Get Employer Profile

#### Input — None (token in header)

#### Output `200 OK`
```json
{
  "uid":          "employer_uid_abc",
  "email":        "ayesha@corp.com",
  "firstName":    "Ayesha",
  "lastName":     "Khan",
  "displayName":  "Ayesha Khan",
  "role":         "employer",
  "jobTitle":     "CEO",
  "phone":        "+923001234567",
  "companyId":    "company_abc123",
  "companyName":  "TechCorp Pvt Ltd",
  "isActive":     true,
  "hierarchyLevel": 0,
  "permissions": {
    "can_view_team_reports": true,
    "can_manage_employees":  true,
    "can_approve_leaves":    true,
    "can_view_analytics":    true,
    "can_create_programs":   true,
    "skip_level_access":     true
  },
  "registeredAt": "2025-01-01T00:00:00+00:00",
  "updatedAt":    "2025-04-05T10:00:00+00:00",
  "company": {
    "id":            "company_abc123",
    "name":          "TechCorp Pvt Ltd",
    "industry":      "Tech",
    "size":          "50-100",
    "ownerId":       "employer_uid_abc",
    "employeeCount": 25,
    "website":       "https://techcorp.pk",
    "address":       "Lahore, Pakistan",
    "phone":         null,
    "description":   null,
    "logoUrl":       null,
    "createdAt":     "2025-01-01T00:00:00+00:00",
    "updatedAt":     "2025-04-05T10:00:00+00:00"
  }
}
```

---

### `PATCH /api/employer/profile` — Update Employer Profile
> **Owner only** (role = employer)

#### Input (all optional — only send fields to change)
```json
{
  "firstName": "Ayesha",
  "lastName":  "Khan Raza",
  "phone":     "+923009999999",
  "jobTitle":  "Founder & CEO"
}
```

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Employer profile updated successfully.",
  "updatedFields": ["lastName", "phone"]
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 400 | No valid fields provided |
| 403 | Caller is not the owner (employer role) |

---

### `GET /api/employer/company` — Get Company Details

#### Input — None

#### Output `200 OK`
```json
{
  "id":            "company_abc123",
  "name":          "TechCorp Pvt Ltd",
  "industry":      "Tech",
  "size":          "50-100",
  "ownerId":       "employer_uid_abc",
  "employeeCount": 25,
  "website":       "https://techcorp.pk",
  "address":       "Lahore, Pakistan",
  "phone":         "+9242111111",
  "description":   "Leading tech company.",
  "logoUrl":       "https://storage.googleapis.com/.../logo.png",
  "createdAt":     "2025-01-01T00:00:00+00:00",
  "updatedAt":     "2025-04-05T10:00:00+00:00"
}
```

---

### `PATCH /api/employer/company` — Update Company Details
> **Owner only**

#### Input (all optional)
```json
{
  "name":        "TechCorp International",
  "industry":    "SaaS",
  "size":        "100-200",
  "website":     "https://techcorp.io",
  "address":     "Karachi, Pakistan",
  "phone":       "+9221111111",
  "description": "A leading SaaS company.",
  "logoUrl":     "https://storage.googleapis.com/.../logo.png"
}
```

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Company details updated successfully.",
  "updatedFields": ["name", "website", "description"]
}
```

> If `name` changes, it is automatically synced to all employees' `company_name` field.

---

### `GET /api/employer/company/stats` — Company Stats

#### Input — None

#### Output `200 OK`
```json
{
  "companyId":           "company_abc123",
  "totalEmployees":      24,
  "activeEmployees":     22,
  "inactiveEmployees":   2,
  "roleBreakdown": {
    "employee": 18,
    "manager":  4,
    "hr":       2
  },
  "departmentBreakdown": {
    "Engineering": 10,
    "Product":     6,
    "HR":          2,
    "Sales":       4,
    "Unassigned":  2
  },
  "recentJoins": 3,
  "computedAt":  "2025-04-05T10:00:00+00:00"
}
```

> `recentJoins` = employees added in the last 30 days.  
> Employer themselves are not counted in employee stats.

---

### `POST /api/employer/change-password` — Change Password
> **Owner only** — Re-authenticates before changing.

#### Input
```json
{
  "current_password": "OldPass@123",
  "new_password":     "NewPass@456"
}
```

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Password changed successfully. Please log in again with your new password."
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 400 | New password < 8 chars or same as current |
| 401 | current_password is wrong |
| 403 | Caller is not the owner |

---

### `DELETE /api/employer/account` — Delete Employer Account ⚠️
> **Owner only** — **Irreversible**

#### Input
```json
{
  "confirmation_phrase": "DELETE MY ACCOUNT",
  "password":            "Secure@123"
}
```

> `confirmation_phrase` must be exactly: `DELETE MY ACCOUNT`

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Employer account and company deleted permanently."
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 400 | confirmation_phrase does not match |
| 401 | Password is incorrect |

---

## 8. EMPLOYEE — NEW OPERATIONS

### `DELETE /api/employees/{uid}` — Permanently Delete Employee
> **Employer only** (not HR) — **Irreversible**  
> For reversible removal, use `/deactivate` instead.

#### Input — None (uid in path)

#### Output `200 OK`
```json
{
  "success": true,
  "uid":     "emp_uid_1",
  "message": "Employee deleted permanently."
}
```

> **Cascade behaviour:**
> - Removed from old manager's `direct_reports[]`
> - Their own direct reports are reassigned upward to the deleted employee's manager
> - Company `employee_count` atomically decremented
> - Firebase Auth account deleted

#### Error Codes
| Status | Condition |
|---|---|
| 403 | Caller is HR (not employer owner) |
| 403 | Employee belongs to different company |
| 404 | Employee not found |

---

### `POST /api/employees/bulk-create` — Bulk Create Employees
> **Employer / HR only** — max 50 per request, partial success allowed

#### Input — JSON array (up to 50 items)
```json
[
  {
    "email":          "ali@corp.com",
    "password":       "Pass123",
    "firstName":      "Ali",
    "lastName":       "Ahmed",
    "role":           "employee",
    "department":     "Engineering",
    "position":       "Developer",
    "phone":          "+923001111111",
    "managerId":      "mgr_uid_xyz",
    "hierarchyLevel": 2
  },
  {
    "email":     "sara@corp.com",
    "password":  "Pass123",
    "firstName": "Sara",
    "lastName":  "Malik",
    "role":      "hr"
  }
]
```

#### Output `201 Created`
```json
{
  "success":   false,
  "created":   1,
  "failed":    1,
  "companyId": "company_abc123",
  "results": [
    {
      "email":   "ali@corp.com",
      "success": true,
      "uid":     "new_uid_abc"
    },
    {
      "email":   "sara@corp.com",
      "success": false,
      "uid":     null,
      "error":   "Email already exists."
    }
  ]
}
```

> `success: false` at top level if ANY item failed. `created` / `failed` give exact counts.  
> Company `employee_count` is incremented once by `created` at the end.

#### Error Codes
| Status | Condition |
|---|---|
| 400 | Empty list |
| 400 | More than 50 items |
| 403 | Not employer/hr |

---

### `PUT /api/employees/{uid}/transfer` — Transfer / Reassign Employee
> **Employer / HR only** — moves employee to new manager / dept / position

#### Input (at least one field required)
```json
{
  "newManagerId":     "new_manager_uid",
  "newDepartment":    "Product",
  "newPosition":      "Product Manager",
  "newHierarchyLevel": 3
}
```

> Set `newManagerId: "none"` to make the employee top-level (no manager).

#### Output `200 OK`
```json
{
  "success": true,
  "uid":     "emp_uid_1",
  "message": "Employee transferred successfully.",
  "changes": {
    "manager_id": {
      "from": "old_mgr_uid",
      "to":   "new_mgr_uid"
    },
    "department": {
      "from": "Engineering",
      "to":   "Product"
    }
  }
}
```

> **Automatic side effects**: Old manager's `direct_reports[]` → removes this uid. New manager's `direct_reports[]` → adds this uid.

#### Error Codes
| Status | Condition |
|---|---|
| 400 | No transfer fields provided |
| 400 | New managerId not in same company |

---

### `GET /api/employees/{uid}/activity` — Employee Activity Summary
> **Employer / HR only** — aggregated stats only, no raw messages ever returned

#### Input — None (uid in path)

#### Output `200 OK`
```json
{
  "uid":              "emp_uid_1",
  "companyId":        "company_abc123",
  "totalCheckIns":    42,
  "totalSessions":    18,
  "lastActiveAt":     "2025-04-04T21:15:00+00:00",
  "avgMoodScore":     6.8,
  "avgStressLevel":   5.2,
  "riskLevel":        "low",
  "sessionModalities": {
    "voice": 11,
    "text":  7
  },
  "computedAt": "2025-04-05T10:00:00+00:00"
}
```

> **Privacy**: No raw check-in answers, no message content, no session transcripts — only aggregated numbers.  
> `riskLevel` is the dominant risk across all check-ins: `low | medium | high | null`  
> `avgMoodScore` and `avgStressLevel` are on a 1–10 scale.

---

## 9. SUPER ADMIN

> **All endpoints require**: `Authorization: Bearer <token>` where token belongs to `role: super_admin`  
> Login: `POST /api/auth/login` with `admin@diltak.ai` / `Diltak#911@`

---

### `GET /api/admin/me` — Super Admin Profile

#### Output `200 OK`
```json
{
  "uid":         "super_admin_uid",
  "email":       "admin@diltak.ai",
  "role":        "super_admin",
  "displayName": "Diltak Super Admin",
  "isActive":    true,
  "createdAt":   "2025-01-01T00:00:00+00:00"
}
```

---

### `GET /api/admin/stats` — Platform-Wide Stats

#### Output `200 OK`
```json
{
  "totalEmployers":  12,
  "totalEmployees":  348,
  "totalCompanies":  12,
  "totalUsers":      361,
  "activeUsers":     322,
  "inactiveUsers":   39,
  "roleBreakdown": {
    "employer":    12,
    "manager":     45,
    "hr":          20,
    "employee":    283,
    "super_admin": 1
  },
  "recentJoins": 18,
  "computedAt":  "2025-04-05T10:00:00+00:00"
}
```

> `recentJoins` = all users created in the last 30 days across the entire platform.

---

### `POST /api/admin/employers` — Create Employer Account ✅
> **Super Admin only** — This is the ONLY way to create an employer.

#### Input
```json
{
  "firstName":   "Ayesha",              // required
  "lastName":    "Khan",               // required
  "email":       "ayesha@corp.com",    // required, unique email
  "password":    "Secure@123",         // required, min 8 chars
  "companyName": "TechCorp Pvt Ltd",   // required, min 2 chars
  "companySize": "50-100",             // optional
  "industry":    "Tech",               // optional
  "jobTitle":    "CEO",                // optional, default "Owner / Founder"
  "phone":       "+923001234567"       // optional
}
```

#### Output `201 Created`
```json
{
  "message":   "Company account created successfully! Please log in to get started.",
  "userId":    "employer_firebase_uid",
  "companyId": "company_employer_firebase_uid",
  "role":      "employer"
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 400 | Password < 8 chars or companyName < 2 chars |
| 401 | No token / invalid token |
| 403 | Caller is not super_admin |
| 409 | Email already registered |
| 500 | Firebase or Firestore error |

---

### `GET /api/admin/employers` — List All Employers

#### Query Parameters
| Param | Type | Default |
|---|---|---|
| `include_inactive` | bool | `false` |

#### Output `200 OK`
```json
{
  "total": 12,
  "employers": [
    {
      "uid":            "employer_uid_abc",
      "email":          "ayesha@corp.com",
      "firstName":      "Ayesha",
      "lastName":       "Khan",
      "displayName":    "Ayesha Khan",
      "role":           "employer",
      "companyId":      "company_abc123",
      "companyName":    "TechCorp Pvt Ltd",
      "department":     null,
      "position":       null,
      "phone":          "+923001234567",
      "jobTitle":       "CEO",
      "hierarchyLevel": 0,
      "isActive":       true,
      "createdAt":      "2025-01-01T00:00:00+00:00",
      "updatedAt":      "2025-04-05T10:00:00+00:00",
      "createdBy":      null
    }
  ]
}
```

---

### `GET /api/admin/employers/{uid}` — Get Single Employer

#### Output `200 OK` — Same shape as single item from the list above

#### Error Codes
| Status | Condition |
|---|---|
| 400 | User exists but is not an employer |
| 404 | Not found |

---

### `PATCH /api/admin/employers/{uid}` — Update Any Employer

#### Input (all optional)
```json
{
  "firstName":      "Ayesha",
  "lastName":       "Khan",
  "phone":          "+923005555555",
  "department":     null,
  "position":       null,
  "jobTitle":       "Founder",
  "hierarchyLevel": 0,
  "isActive":       true,
  "role":           "employer"
}
```

#### Output `200 OK`
```json
{
  "success": true,
  "message": "User updated successfully.",
  "updatedFields": ["jobTitle", "isActive"]
}
```

> Setting `isActive: false` also sets Firebase Auth `disabled: true` automatically.

---

### `POST /api/admin/employers/{uid}/deactivate` — Deactivate Employer

#### Output `200 OK`
```json
{ "success": true, "message": "User deactivated successfully." }
```

---

### `POST /api/admin/employers/{uid}/reactivate` — Reactivate Employer

#### Output `200 OK`
```json
{ "success": true, "message": "User reactivated successfully." }
```

---

### `DELETE /api/admin/employers/{uid}` — Hard-Delete Employer ⚠️

> Deletes: Firestore user profile + company document + Firebase Auth account.  
> Employee accounts are **not** deleted — their `company_id` is preserved.

#### Input — None

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Employer deleted."
}
```

---

### `GET /api/admin/employees` — List All Employees (Cross-Company)

#### Query Parameters
| Param | Type | Default | Notes |
|---|---|---|---|
| `company_id` | string | `null` | Filter to a single company |
| `role` | string | `null` | employee / manager / hr |
| `include_inactive` | bool | `false` | |

#### Output `200 OK`
```json
{
  "total": 348,
  "employees": [
    {
      "uid":         "emp_uid_1",
      "email":       "ali@corp.com",
      "firstName":   "Ali",
      "lastName":    "Ahmed",
      "role":        "employee",
      "companyId":   "company_abc123",
      "companyName": "TechCorp Pvt Ltd",
      "department":  "Engineering",
      "isActive":    true,
      "createdAt":   "2025-03-01T00:00:00+00:00"
    }
  ]
}
```

---

### `GET /api/admin/employees/{uid}` — Get Any Employee

#### Output `200 OK` — Same shape as item above

---

### `PATCH /api/admin/employees/{uid}` — Update Any Employee

#### Input / Output — Same schema as `PATCH /api/admin/employers/{uid}`

---

### `DELETE /api/admin/employees/{uid}` — Hard-Delete Any Employee

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Employee deleted."
}
```

> **Cascade**: Removes from manager's `direct_reports`, reassigns their reports upward, decrements company count, deletes Firebase Auth.

---

### `GET /api/admin/companies` — List All Companies

#### Output `200 OK`
```json
{
  "total": 12,
  "companies": [
    {
      "id":            "company_abc123",
      "name":          "TechCorp Pvt Ltd",
      "industry":      "Tech",
      "size":          "50-100",
      "ownerId":       "employer_uid_abc",
      "employeeCount": 25,
      "website":       "https://techcorp.pk",
      "description":   null,
      "createdAt":     "2025-01-01T00:00:00+00:00",
      "updatedAt":     "2025-04-05T10:00:00+00:00"
    }
  ]
}
```

---

### `GET /api/admin/companies/{company_id}` — Get Company

#### Output `200 OK` — Full company document (same as `/api/employer/company` but without auth scope restriction)

---

### `PATCH /api/admin/companies/{company_id}` — Update Any Company

#### Input (all optional)
```json
{
  "name":        "TechCorp International",
  "industry":    "SaaS",
  "size":        "100-200",
  "website":     "https://techcorp.io",
  "address":     "Karachi, Pakistan",
  "phone":       "+9221000000",
  "description": "Updated description.",
  "logoUrl":     "https://..."
}
```

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Company updated successfully.",
  "updatedFields": ["name", "industry"]
}
```

> If `name` is updated, all employees in the company have their `company_name` field synced.

---

### `POST /api/admin/users/{uid}/reset-password` — Force Reset Any User's Password

#### Input
```json
{
  "new_password": "NewSecurePass@123"
}
```

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Password reset successfully for user emp_uid_1."
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 400 | New password < 8 chars |
| 500 | Firebase Auth update failed |

---

### `POST /api/admin/change-password` — Change Super Admin Password

#### Input
```json
{
  "current_password": "Diltak#911@",
  "new_password":     "NewAdminPass@2025"
}
```

#### Output `200 OK`
```json
{
  "success": true,
  "message": "Super admin password changed. Please log in again."
}
```

#### Error Codes
| Status | Condition |
|---|---|
| 400 | New password < 8 chars or same as current |
| 401 | Current password wrong |

---

## TypeScript Types — New (Add to `types/api.ts`)

```typescript
// ── Employer CRUD ────────────────────────────────────────────────────────────

export interface EmployerProfile {
  uid:            string;
  email:          string;
  firstName:      string;
  lastName:       string;
  displayName:    string;
  role:           'employer';
  jobTitle:       string | null;
  phone:          string | null;
  companyId:      string;
  companyName:    string;
  isActive:       boolean;
  hierarchyLevel: number;
  permissions:    Record<string, boolean>;
  registeredAt:   string | null;
  updatedAt:      string | null;
  company:        CompanyDetails | null;
}

export interface CompanyDetails {
  id:            string;
  name:          string;
  industry:      string | null;
  size:          string | null;
  ownerId:       string;
  employeeCount: number;
  website:       string | null;
  address:       string | null;
  phone:         string | null;
  description:   string | null;
  logoUrl:       string | null;
  createdAt:     string | null;
  updatedAt:     string | null;
}

export interface CompanyStats {
  companyId:           string;
  totalEmployees:      number;
  activeEmployees:     number;
  inactiveEmployees:   number;
  roleBreakdown:       Record<string, number>;
  departmentBreakdown: Record<string, number>;
  recentJoins:         number;
  computedAt:          string;
}

export interface UpdateEmployerProfileRequest {
  firstName?: string;
  lastName?:  string;
  phone?:     string;
  jobTitle?:  string;
}

export interface UpdateCompanyRequest {
  name?:        string;
  industry?:    string;
  size?:        string;
  website?:     string;
  address?:     string;
  phone?:       string;
  description?: string;
  logoUrl?:     string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password:     string;
}

export interface DeleteAccountRequest {
  confirmation_phrase: 'DELETE MY ACCOUNT';
  password:            string;
}

// ── Employee New Operations ───────────────────────────────────────────────────

export interface BulkCreateItem {
  email:           string;
  password:        string;
  firstName:       string;
  lastName:        string;
  role?:           'employee' | 'manager' | 'hr';
  department?:     string;
  position?:       string;
  phone?:          string;
  managerId?:      string;
  hierarchyLevel?: number;
}

export interface BulkCreateResult {
  email:   string;
  success: boolean;
  uid:     string | null;
  error:   string | null;
}

export interface BulkCreateResponse {
  success:   boolean;
  created:   number;
  failed:    number;
  results:   BulkCreateResult[];
  companyId: string;
}

export interface TransferEmployeeRequest {
  newManagerId?:      string | null;  // 'none' = remove manager
  newDepartment?:     string;
  newPosition?:       string;
  newHierarchyLevel?: number;
}

export interface TransferEmployeeResponse {
  success: boolean;
  uid:     string;
  message: string;
  changes: Record<string, { from: unknown; to: unknown }>;
}

export interface ActivitySummary {
  uid:               string;
  companyId:         string;
  totalCheckIns:     number;
  totalSessions:     number;
  lastActiveAt:      string | null;
  avgMoodScore:      number | null;   // 1–10
  avgStressLevel:    number | null;   // 1–10
  riskLevel:         'low' | 'medium' | 'high' | null;
  sessionModalities: Record<string, number>;
  computedAt:        string;
}

// ── Super Admin ───────────────────────────────────────────────────────────────

export interface PlatformStats {
  totalEmployers:  number;
  totalEmployees:  number;
  totalCompanies:  number;
  totalUsers:      number;
  activeUsers:     number;
  inactiveUsers:   number;
  roleBreakdown:   Record<string, number>;
  recentJoins:     number;
  computedAt:      string;
}

export interface AdminUserProfile {
  uid:            string;
  email:          string;
  firstName:      string;
  lastName:       string;
  displayName:    string;
  role:           string;
  companyId:      string | null;
  companyName:    string | null;
  department:     string | null;
  position:       string | null;
  phone:          string | null;
  jobTitle:       string | null;
  hierarchyLevel: number;
  isActive:       boolean;
  createdAt:      string | null;
  updatedAt:      string | null;
  createdBy:      string | null;
}

export interface AdminUpdateUserRequest {
  firstName?:      string;
  lastName?:       string;
  phone?:          string;
  department?:     string;
  position?:       string;
  jobTitle?:       string;
  hierarchyLevel?: number;
  isActive?:       boolean;
  role?:           string;
}

export interface ResetPasswordRequest {
  new_password: string;   // min 8 chars
}

export interface MutationResponse {
  success:       boolean;
  message:       string;
  updatedFields?: string[];
}
```

---

## Role Access Summary (all endpoints combined)

| Action | super_admin | employer | hr | manager | employee |
|---|---|---|---|---|---|
| Login / Register | ✅ | ✅ | ✅ | ✅ | ✅ |
| Create employee | ✅ | ✅ | ✅ | ❌ | ❌ |
| Bulk create employees | ✅ | ✅ | ✅ | ❌ | ❌ |
| List employees | ✅ | ✅ | ✅ | ❌ | ❌ |
| Update employee | ✅ | ✅ | ✅ | ❌ | ❌ |
| Transfer employee | ✅ | ✅ | ✅ | ❌ | ❌ |
| View employee activity | ✅ | ✅ | ✅ | ❌ | ❌ |
| Soft deactivate/reactivate | ✅ | ✅ | ✅ | ❌ | ❌ |
| Hard delete employee | ✅ | ✅ | ❌ | ❌ | ❌ |
| Update employer profile | ✅ | ✅ (own) | ❌ | ❌ | ❌ |
| Update company | ✅ | ✅ (own) | ❌ | ❌ | ❌ |
| View company stats | ✅ | ✅ | ✅ | ❌ | ❌ |
| Delete employer account | ✅ | ✅ (own) | ❌ | ❌ | ❌ |
| List ALL employers/companies | ✅ | ❌ | ❌ | ❌ | ❌ |
| Force reset any password | ✅ | ❌ | ❌ | ❌ | ❌ |
| Platform stats | ✅ | ❌ | ❌ | ❌ | ❌ |
| Analytics dashboards | ✅ | ✅ | ✅ | ❌ | ❌ |

---

## 6. CHAT & REPORTS

### `POST /api/chat_wrapper` — Interactive Chat & End-Session Reports
> **Protected** — JWT required.

#### Input (JSON or Multipart Form)
```json
{
  "messages": [
    {
      "role": "user",
      "content": "Hello",
      "sender": "user"
    }
  ],
  "endSession": false,
  "sessionType": "text",
  "sessionDuration": 0,
  "userId": "emp_uid",
  "companyId": "company_uid",
  "umaSessionId": "session_id_here"
}
```

#### Output (Message Response)
```json
{
  "type": "message",
  "data": {
    "content": "Hi there!",
    "sender": "ai",
    "umaSessionId": "session_id_here",
    "emotion": "Happy",
    "avatarEmotion": "HAPPY",
    "emotionIntensity": 0.8,
    "expressionStyle": "warm",
    "conversationPhase": "opening"
  }
}
```

#### Output (Report Response — if `endSession: true`)
```json
{
  "type": "report",
  "data": {
    "meta": {
      "report_id": "...",
      "user_id": "emp_uid",
      "generated_at": "2025-04-05T04:00:00+00:00",
      "version": "1.0"
    },
    "employee_id": "emp_uid",
    "company_id": "company_uid",
    "session_type": "text",
    "session_duration_minutes": 0,
    "mental_health": {
      "score": 6.5,
      "level": "medium",
      "confidence": 0.9,
      "trend": "stable",
      "summary": "...",
      "metrics": {
         "stress_anxiety": { "score": 7.0, "level": "high", "reason": "...", "weight": 1.0 }
      }
    },
    "physical_health": {
       "score": 5.0,
       "level": "medium",
       "confidence": 0.8,
       "trend": "stable",
       "summary": "...",
       "metrics": { ... }
    },
    "overall": {
      "score": 5.8,
      "level": "medium",
      "confidence": 0.85,
      "trend": "stable",
      "priority": "medium",
      "summary": "...",
      "full_report": "...",
      "key_insights": ["..."],
      "strengths": ["..."],
      "risks": ["..."],
      "recommendations": ["..."]
    }
  }
}
```

---

### `POST /api/chat_wrapper/analyze` — Standalone Chat Analysis
> **Protected** — JWT required. Generates full wellness report independently without ending a session.

#### Input
```json
{
  "user_id": "emp_uid",
  "messages": [
    {
      "role": "user",
      "content": "I'm so stressed"
    },
    {
      "role": "assistant",
      "content": "Tell me more"
    }
  ]
}
```

#### Output `200 OK`
```json
{
  "meta": {
    "report_id": "...",
    "user_id": "emp_uid",
    "generated_at": "2025-04-05T04:00:00+00:00",
    "version": "1.0"
  },
  "mental_health": { ... },
  "physical_health": { ... },
  "overall": {
    "score": 5.8,
    "level": "medium",
    "confidence": 0.85,
    "trend": "stable",
    "priority": "medium",
    "summary": "...",
    "full_report": "...",
    "key_insights": ["..."],
    "strengths": ["..."],
    "risks": ["..."],
    "recommendations": ["..."]
  }
}
```
