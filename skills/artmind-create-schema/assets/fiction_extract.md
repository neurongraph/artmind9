# Sample Extract — Fiction Domain

**Source:** *The Adventure of the Copper Beeches* by Arthur Conan Doyle  
**Purpose:** Representative fiction domain document. This is the kind of text that produced `fiction_schema.yaml`.

---

"To the man who loves art for its own sake," remarked Sherlock Holmes, tossing aside the advertisement sheet of the *Daily Telegraph*, "it is frequently in its least important and lowliest manifestations that the keenest pleasure is to be derived. It is pleasant to me to observe, Watson, that you have so far grasped this truth that in these little records of our cases which you have been good enough to draw up, and, I am bound to say, occasionally to embellish, you have given prominence not so much to the many causes célèbres and sensational trials in which I have figured, but rather to those incidents which may have been trivial in themselves, but which have given room for those faculties of deduction and of logical synthesis which I have made my special province."

Miss Violet Hunter was a young woman of striking appearance, with a bright, eager face and a mass of flaxen hair. She had come to Baker Street in some agitation, having been offered a position as governess at a country house called the Copper Beeches, near Winchester, by one Mr. Jephro Rucastle — a position that paid five times the usual wage, on the condition that she cut her long hair short.

"It is with a heavy heart that I take up my pen to write these the last words in which I shall ever record the singular gifts by which my friend Mr. Sherlock Holmes was distinguished," Holmes said to Watson, leaning back in his armchair and steepling his fingers. "The strange thing is that this case, which is in many ways the most complex I have encountered, hinges on something perfectly simple."

The Copper Beeches itself was a large, rambling house set back from the Winchester road, surrounded by a grove of copper beeches whose dark foliage gave the property an oppressive, secretive atmosphere. Holmes and Watson arrived to find Miss Hunter in considerable distress. She had been required to sit at a particular window in a particular dress — a dress that had been provided for her, not her own — while a gentleman outside watched from the road. She had also heard noises from the locked room at the end of the west corridor, and had twice glimpsed, at the edge of the grounds, a pale face pressed against the iron gate.

Rucastle himself was a heavy-set, jovial man with an air of false bonhomie that Holmes had immediately distrusted. His wife, thin and silent, barely spoke. Their young son Edward was a cruel child who enjoyed tormenting insects and small animals. The only other member of the household was Toller, the surly groom, and his wife — who, as it transpired, kept the key that mattered most.

"It has long been an axiom of mine," said Holmes, "that the little things are infinitely the most important. The matter of the photograph, the matter of the dress, the matter of the road watcher — these are not separate mysteries. They are three facets of a single deception."

The locked room, when at last they forced the door, revealed Miss Alice Rucastle — Jephro's daughter from his first marriage — kept prisoner in her own father's house to prevent her from communicating with the solicitor Mr. Fowler, to whom she had been engaged and whose claim on her inheritance was the root of everything.

---

**Note for schema author:** This extract demonstrates the full range of fiction entity classes:
- **CHARACTERS:** Holmes, Watson, Violet Hunter, Jephro Rucastle, Alice Rucastle, Edward Rucastle, Toller, Mrs. Toller, Mr. Fowler
- **LOCATIONS:** Baker Street, Copper Beeches, Winchester, the locked room, the west corridor
- **OBJECTS:** the photograph, Miss Hunter's cut hair, the governess dress, the iron gate, the key
- **EVENTS:** Holmes and Watson's arrival, the staged window scenes, Alice's imprisonment, the revelation
- **CONCEPT:** the deception, the inheritance scheme, Holmes's axiom about small details
- **ORGANIZATION:** not prominent here, but a criminal syndicate or household staff could appear in other stories

Relationships visible in this extract include: `employs` (Rucastle → Hunter), `imprisoned_at` (Alice → locked room), `investigates` (Holmes → the deception), `visits` (Holmes/Watson → Copper Beeches), `involves` (the deception → multiple characters), and `resides_at` for both Baker Street and the Beeches.
