# Sample Extract — Personal Journal Domain

**Source:** Personal diary entries, April 2026  
**Purpose:** Representative personal_journal domain document. This is the kind of text that produced `personal_journal_schema.yaml`.

---

## April 15, 2026

Woke up late today. The alarm didn't go off properly. Preeti had already packed the kids' lunch by the time I got to the kitchen. Neetish had a math test scheduled for today so she was nervous. Mohan, as usual, was busy playing with Browny in the living room.

Office was busy with the quarterly reviews. Had back-to-back meetings from 9 AM to 4 PM. By evening, I was exhausted. The project deadline is approaching and the team is working hard to meet it.

Preeti picked up the kids from school. She mentioned that Neetish did well in her science project presentation. Mohan has been fighting with his friend from school. Need to talk to him about it.

Browny needed his evening walk. Took him to the nearby park. He saw a stray dog and started barking loudly. Had to pull him away. He's getting stubborn with age.

Preeti made paneer for dinner. The kids loved it. Watched some TV before sleeping. Early start tomorrow.

---

## April 18, 2026

- Neetish scored 85 in her math test. She was hoping for more but we're proud.
- Office: received an email about the promotion review next month.
- Preeti enrolled for a yoga class near home. She starts next week.
- Mohan brought home a drawing he made in school. It was of our family including Browny.
- Evening: took Browny to the vet for his annual vaccination.
- The vet said Browny is healthy but needs to lose some weight.
- Started a diet for Browny. He's not happy about it.
- Dinner: Preeti made rajma chawal. Kids loved it.
- Helped Neetish with her history project. She's doing it on Maratha history.
- Early night today. Long day ahead tomorrow.

---

## April 22, 2026

Today was a mix of good and challenging moments.

Neetish had her history presentation in school. She was nervous but did wonderfully. Preeti and I both went to watch. She's growing up so fast. Proud parents moment.

Office was demanding. The client rejected one of our proposals. Need to rework the entire strategy. The team is a bit disappointed but we'll bounce back.

Met with Mohan's principal regarding the Aryan situation. It turns out the fight was mostly a misunderstanding. Both boys want to resolve it. Organised a playdate for them this weekend to mend things.

Preeti is tired from the yoga class. She wants to go again tomorrow. The instructor is good.

Evening: Browny and I went to the Meghdoot Garden. It's peaceful there in the evenings. Saw many families with kids. Indore has such nice spots.

Dinner was leftovers from yesterday. The kids didn't complain. Easy day.

Sleep came early. Tomorrow is another busy day.

---

**Note for schema author:** These entries demonstrate the full range of personal_journal entity classes:
- **PERSON:** the diarist (self), Preeti (spouse), Neetish (daughter), Mohan (son), Browny (pet), Aryan (friend), the principal, the yoga instructor, the vet
- **PLACE:** home, office, school, nearby park, Meghdoot Garden, vet clinic
- **EVENT:** math test, history presentation, vet visit, meeting with principal, quarterly reviews, promotion email
- **ACTIVITY:** evening walk, yoga class, helping with homework, cooking dinner, watching TV
- **EMOTION:** pride (in Neetish's presentation), exhaustion (after back-to-back meetings), worry (about Mohan's conflict), contentment (Meghdoot Garden)
- **PLAN:** talk to Mohan about the fight, playdate for Mohan and Aryan, Browny's new diet

Key relationship patterns visible in these entries: `is_proud_of` (diarist → Neetish), `worries_about` (diarist → Mohan), `conflicts_with` (Mohan ↔ Aryan), `performs` (Preeti → yoga), `occurs_at` (history presentation → school), `evokes` (presentation → pride), `has_plan` (diarist → playdate). The entries are first-person so the diarist is always an implicit source node — extract them as a PERSON entity with type `self`.
