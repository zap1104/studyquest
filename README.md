# 🎮 StudyQuest

StudyQuest is an AI-assisted learning platform that transforms uploaded academic materials into structured courses with chapter reviews, mixed assessments, progression rules, and game-inspired motivation.

Learners can upload a syllabus, module, reviewer, or other supported study material. StudyQuest uses Gemini to create a personalized learning path containing structured chapter content and secure, server-graded assessments.

> **Project status:** Active development. The end-to-end course generation, study, assessment, progression, usage-limit, and responsive mobile flows are implemented. Remaining work focuses on gamification refinement, security hardening, production telemetry, and deployment.

## Core learning flow

1. **Create a course** by uploading supported study material.
2. Select a study mode and one or more practice assessment formats.
3. StudyQuest generates a structured course using Gemini 3.6 Flash.
4. Review each chapter through organized learning content.
5. Complete a secure mixed assessment with immediate educational feedback.
6. Review submitted answers, points, explanations, and missed concepts.
7. Complete or test out of chapters to advance through the learning path.

```text
Create Course → Review Chapter → Take Quiz → Review Results → Continue
```

## Current features

### AI-assisted course generation

- Upload-based course creation
- Gemini 3.6 Flash integration
- Pydantic-validated structured generation
- Generated course, chapter, review, and assessment data
- Application-controlled assessment distribution
- Generation recovery and validation safeguards
- Prototype/mock generation mode for local development

### Structured chapter reviews

Generated reviews may include:

- Chapter overview and focus
- Learning objectives
- Structured topic sections
- Key concepts and definitions
- Simple explanations and analogies
- Comparison matrices
- High-yield enumerations
- Common misconceptions and exam traps
- Key takeaways

### Mixed assessments

StudyQuest supports four question types:

- Multiple Choice
- True or False
- Identification
- Enumeration

Essay questions are intentionally excluded.

Assessment features include:

- Type-specific rendering
- Server-side answer checking
- Authoritative final grading
- Text normalization for Identification
- Accepted answer variants
- Enumeration partial credit
- Ordered and unordered Enumeration support
- Immediate explanations
- Mixed-format Answer Review
- Safe initial payloads without exposed answer keys

### Course progression

- Real course progress based on completed chapters
- Available, completed, tested-out, and locked chapter states
- Reading-to-end completion gate
- Direct URL protection for locked content
- Guided prerequisite completion
- Quiz-based test-out route
- Dedicated locked-chapter requirements screen

The current progression policy uses:

```text
Guided completion: Read and mark the lesson complete, then score 75% or higher on the quiz
Test-out completion: Score 75% or higher on the quiz without completing the lesson
```

### Plans, quotas, and course credits

StudyQuest includes prototype Free and Plus plan states.

#### StudyQuest Free

- 1 standard course generation per rolling 7-day period
- Up to 5 active courses
- Up to 1 stored completion bonus credit
- Core study modes and assessment formats
- Limited sponsored placement

#### StudyQuest Plus

- 3 standard course generations per rolling 7-day period
- Up to 15 active courses
- Up to 2 stored completion bonus credits
- Larger study-material allowance
- Expanded analytics direction
- Ad-free experience

No live billing or payment gateway is connected. Developers can change plan state through Django Admin or development tooling.

### Course management

- Active and archived course states
- Course Library
- Course creation limits based on plan
- Archive and restore workflows
- Course and chapter renaming
- Course deletion with confirmation
- Completion-credit transaction history

### Gamification

- XP rewards
- Levels
- Study streaks
- Achievements
- Daily goals
- Progress indicators
- Completion and milestone feedback
- Anti-farming safeguards for repeated quiz attempts

### Mobile inclusivity

The core StudyQuest flow is responsive across desktop, tablet, portrait mobile, and phone landscape layouts.

Mobile features include:

- Responsive navigation drawer
- Mobile plan summary
- Touch-friendly controls
- Responsive Dashboard, Course Creation, and Course Library
- Mobile Learning Path and locked-chapter layouts
- Full-screen Chapter Review reader
- Mobile-safe Identification and Enumeration inputs
- Slide-up assessment feedback drawer
- Safe-area-aware bottom actions
- Portrait and landscape viewport handling
- Responsive sponsor and plan-comparison dialogs

## Tech stack

- **Backend:** Django 5.2 and Python
- **Database:** SQLite for local development
- **Validation:** Pydantic
- **AI:** Gemini 3.6 Flash
- **Frontend:** Django templates, vanilla CSS, and vanilla JavaScript
- **Document intake:** Uploaded academic files processed server-side
- **Authentication:** Django authentication
- **Development tooling:** Django Admin, management commands, and test fixtures

## Project structure

```text
studyquest/
├── manage.py
├── requirements.txt
├── studyquest/                     # Project settings and root URLs
├── courses/                        # Main StudyQuest application
│   ├── migrations/
│   ├── management/commands/        # Development and administrative commands
│   ├── static/courses/
│   │   ├── css/
│   │   ├── images/
│   │   ├── js/
│   │   └── videos/
│   ├── templates/courses/
│   │   ├── components/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── course_form.html
│   │   ├── course_list.html
│   │   ├── course_detail.html
│   │   ├── chapter_review.html
│   │   ├── chapter_locked.html
│   │   └── chapter_quiz.html
│   ├── models.py
│   ├── schemas.py                 # Structured generation contracts
│   ├── services.py                # Gemini and generation services
│   ├── plan_policies.py           # Free and Plus plan rules
│   ├── views.py
│   ├── urls.py
│   └── tests/
├── docs/
│   ├── PROJECT_BRIEF.md
│   └── StudyQuest_Product_Vision_Post_Mobile_Backlog.md
├── setup.bat
├── setup.sh
├── run.bat
└── run.sh
```

> The exact module names may evolve as services and policies are separated further. Use the repository as the source of truth when a path differs from this overview.

## Getting started

### Automated setup

On Windows, run:

```powershell
.\setup.bat
```

On macOS or Linux, run:

```bash
chmod +x setup.sh run.sh
./setup.sh
```

After setup, start the application:

**Normal daily startup (Windows one-click):**

```powershell
.\start_studyquest.bat
```

This opens both the web application terminal (`run.bat` at http://127.0.0.1:8000/) and the background generation worker terminal (`run_worker.bat`) automatically.

**Manual startup (separate terminals):**

```powershell
.\run.bat          # Terminal 1: web application
.\run_worker.bat   # Terminal 2: course generation worker
```

**macOS or Linux:**

```bash
./run.sh                                       # Terminal 1: web application
python manage.py run_course_generation_worker  # Terminal 2: course worker
```

Course generation is **asynchronous**. The web server queues the work; the separate worker process performs it. Without the worker running, submitted courses remain safely queued until the worker is started. See [docs/COURSE_FORGE.md](docs/COURSE_FORGE.md) for worker details, failure recovery, and retry behavior.

### Manual setup

Create and activate a virtual environment:

```powershell
python -m venv venv
venv\Scripts\activate
```

For macOS or Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies and prepare the database:

```bash
pip install -r requirements.txt
python manage.py migrate
```

Create an administrator account if needed:

```bash
python manage.py createsuperuser
```

Start the development server:

```bash
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

## Environment configuration

Copy `.env.example` to `.env` and fill in real values. `.env` is gitignored
and must never be committed; `studyquest/settings.py` loads it automatically.

```text
DJANGO_SECRET_KEY   # required — app refuses to start without it when DEBUG is off
GEMINI_API_KEY      # required for AI course generation
DEBUG               # True for local development, False (or unset) when deployed
ALLOWED_HOSTS       # comma-separated; defaults to 127.0.0.1,localhost
```

Generate a secret key with:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

`DEBUG=False` automatically enables HTTPS redirect, secure cookies, HSTS and
`X_FRAME_OPTIONS: DENY`. Verify a deployment configuration with:

```bash
python manage.py check --deploy
```

When testing from a physical phone, add your machine's LAN IP to
`ALLOWED_HOSTS` in your local `.env` — not in source.

See [docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md) for the hardening
checklist and its current status.

## Running checks and tests

Run Django's system checks:

```bash
python manage.py check
```

Run the application tests:

```bash
python manage.py test courses
```

Before opening or merging a pull request, also verify the core flow manually:

```text
Create Course
→ Open Learning Path
→ Review Chapter
→ Mark Lesson Complete
→ Take Mixed Assessment
→ Review Results
→ Continue to Next Chapter
```

## Mobile testing

### Browser emulation

Test at minimum:

```text
Portrait
360 × 800
390 × 844
412 × 915
768 × 1024

Landscape
800 × 360
844 × 390
915 × 412

Desktop
1366 × 768
1920 × 1080
```

### Physical phone testing

Connect the development computer and phone to the same private network.

Find the computer's local IPv4 address, add it to `ALLOWED_HOSTS`, and run:

```bash
python manage.py runserver 0.0.0.0:8000
```

Then open the local address from the phone:

```text
http://YOUR_LOCAL_IP:8000/
```

Physical-device testing is especially important for:

- File selection
- Touch targets
- Software keyboard behavior
- Chapter Review scrolling
- Reading completion
- Assessment feedback drawers
- Portrait-to-landscape rotation
- Safe-area spacing

## Development workflow

Create feature branches from the appropriate stable base:

```bash
git switch main
git pull origin main
git switch -c jendrick/feature-name
```

Recommended workflow:

1. Keep each commit focused on one stable milestone.
2. Run checks and tests before committing.
3. Push regularly for remote backup.
4. Open a draft pull request for work that still needs integration testing.
5. Merge only after regression and device testing pass.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the repository workflow.

## Roadmap

### Completed foundations

- [x] Core Django models and structured schemas
- [x] Gemini course generation integration
- [x] File-based course creation
- [x] Authentication and per-user progress
- [x] Mixed assessment generation and grading
- [x] Reading-gated chapter progression
- [x] Locked chapters and test-out flow
- [x] Course usage limits and plan states
- [x] Course archiving and restoration
- [x] Mobile inclusivity across core learning flows

### Next

- [ ] Refine the teaching-focused Quiz Review experience
- [ ] Add anchored navigation to structured Chapter Review sections
- [ ] Complete gamification and milestone feedback
- [ ] Expand security hardening and endpoint rate limiting
- [ ] Optimize database queries and generation telemetry
- [ ] Complete production deployment and browser certification

### Future exploration

- [ ] Flashcard decks and spaced repetition
- [ ] Adaptive Daily Review based on weak concepts
- [ ] PDF and Anki study-material export
- [ ] Progressive Web App installation
- [ ] Private study groups and shared course milestones

See `docs/StudyQuest_Product_Vision_Post_Mobile_Backlog.md` for the preserved post-mobile product backlog and implementation guidance.

## Product language

StudyQuest uses clear educational terminology as the primary interface language.

Preferred terms include:

- Create Course
- Course Library
- Learning Path
- Chapter Review
- Quiz
- Quiz Review
- Complete Lesson

Game-inspired language is used as supporting personality rather than as a replacement for clear navigation and accessibility labels.

## Security notes

- Correct answers are not included in the initial browser payload.
- Immediate feedback is graded by Django using stored authoritative data.
- Final quiz submission regrades answers server-side.
- Client-claimed correctness is ignored.
- Chapter access is protected on the server.
- Course records are scoped to their owners.
- API keys and production secrets must never be committed.

## About

StudyQuest began as a Technopreneurship course project and has evolved into a functional AI-assisted learning platform focused on structured review, secure assessment, earned progression, and mobile-inclusive study.

See [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) for the original project pitch and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance.
