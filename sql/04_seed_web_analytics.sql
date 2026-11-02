-- =============================================================
-- SkillMap: migration 04 — web analytics tools
-- Run after 03. Safe to re-run (ON CONFLICT DO NOTHING).
--
-- Why: the CR marketing-analytics roles (e.g. Critical Mass "Marketing Science
-- Analyst") ask for Google Analytics / Adobe Analytics / tag management, which the
-- dictionary didn't have; one posting got no tools at all.
--
-- Not added as a synonym: 'gtm'. In job ads it almost always means go-to-market
-- (309 of today's postings use "GTM", e.g. "Financial Analyst, GTM").
-- =============================================================


INSERT INTO skills (skill_name, category) VALUES
    ('Google Analytics',   'Web Analytics'),
    ('Adobe Analytics',    'Web Analytics'),
    ('Google Tag Manager', 'Web Analytics')
ON CONFLICT (skill_name) DO NOTHING;

-- Synonyms (always lowercase)
INSERT INTO skill_synonyms (synonym, skill_id)
SELECT v.synonym, s.skill_id
FROM (VALUES
    ('google analytics',    'Google Analytics'),
    ('ga4',                 'Google Analytics'),
    ('universal analytics', 'Google Analytics'),
    ('adobe analytics',     'Adobe Analytics'),
    ('omniture',            'Adobe Analytics'),
    ('google tag manager',  'Google Tag Manager')
) AS v(synonym, skill_name)
JOIN skills s ON s.skill_name = v.skill_name
ON CONFLICT (synonym) DO NOTHING;

