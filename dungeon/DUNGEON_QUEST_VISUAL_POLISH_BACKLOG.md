# Dungeon Quest — Visual Polish Backlog

This backlog records non-blocking cosmetic and aesthetic polish opportunities identified during Phase 0 (Workspace Stabilization & Combo Mechanics). 

Per the roadmap release gate, Phase 0 is officially closed on functional, interaction, and responsive stability. These visual polish items are safely deferred and prioritized here for future polish passes after the Phase 1 Review Run feature is delivered.

---

## 1. Frame & Corner Accents
- **Observation**: The purple corner accents on `.dungeon-hud` and `.dungeon-battle` resemble development resize handles.
- **Goal**: Refine into subtle retro dungeon stonework bevels, crisp inner borders, or clean minimalist framing without corner bracket visual noise.

## 2. Left Status Panel Space Balancing
- **Observation**: On tall displays (e.g. 1080p, 1440p viewports), substantial unused vertical space remains in the Left column below the Items list.
- **Goal**: Distribute spacing harmoniously or introduce secondary contextual information (such as run turn counter, active streak status, or miniature dungeon map coordinates).

## 3. Potion Card Inactive Contrast
- **Observation**: Empty potion slots currently carry relatively low contrast against dark backgrounds.
- **Goal**: Render empty potion slots with high-contrast dashed silhouettes, distinct empty flask icons, and clear `0 / 3` quantity readouts.

## 4. Encounter Fieldset Spacing Consistency
- **Observation**: The vertical rhythm inside `.dungeon-battle-body` varies slightly between Multiple Choice, True/False cards, and Enumeration inputs.
- **Goal**: Establish unified margin and padding tokens across all question layout renderers.

## 5. Objective Bar & Canvas Alignment
- **Observation**: The objective bar and room canvas frame in the Center column could align with more unified margins and borders.
- **Goal**: Align the outer border edges of `.dungeon-objective-bar` and `.dungeon-board-panel` to identical column widths and coordinate corner radiuses.

## 6. Room Caption Proximity
- **Observation**: Movement instructions ("Arrow keys or WASD to move. Grass hides enemies.") are visually distanced from the room frame.
- **Goal**: Integrate the caption into the board frame footer or dock directly below the canvas.

## 7. Action Button Prominence
- **Observation**: The `.btn-primary` Attack / Next Question button is compact and narrow relative to the width of the encounter panel.
- **Goal**: Style the battle action button with full width (`width: 100%`) or chunky retro arcade button aesthetics.

## 8. Page Header Vertical Economy
- **Observation**: The standard page header (`.page-header.dungeon-header`) consumes vertical height on 1080px screens.
- **Goal**: Condense into an inline title strip with a compact icon-based "Abandon run" button.

## 9. Transition Between Battle Resolution & Roaming
- **Observation**: When an enemy is defeated, the objective bar immediately shows "Explore the room", while the right battle panel displays the final feedback until "Continue" is clicked.
- **Goal**: Add a clear "Enemy Defeated! / Room Clear" victory banner to the feedback header before transitioning back to the idle card (`data-battle-idle`).

## 10. Audio & Advanced Particle Assets (Deferred to Phase 3)
- **Observation**: Attacks and key pickups rely solely on visual canvas flashes.
- **Goal**: Add 8-bit / 16-bit sound effect cues (synthesized via Web Audio API with a mute toggle), particle bursts for key pickups, and course-themed room color palettes.
