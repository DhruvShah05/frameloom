---
name: Loom
colors:
  surface: '#fcf9f8'
  surface-dim: '#dcd9d9'
  surface-bright: '#fcf9f8'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f6f3f2'
  surface-container: '#f0eded'
  surface-container-high: '#eae7e7'
  surface-container-highest: '#e5e2e1'
  on-surface: '#1b1b1c'
  on-surface-variant: '#504534'
  inverse-surface: '#303030'
  inverse-on-surface: '#f3f0ef'
  outline: '#827562'
  outline-variant: '#d4c4ae'
  surface-tint: '#7d5800'
  primary: '#7d5800'
  on-primary: '#ffffff'
  primary-container: '#c98f00'
  on-primary-container: '#432e00'
  inverse-primary: '#fcbb3b'
  secondary: '#775a00'
  on-secondary: '#ffffff'
  secondary-container: '#fdc416'
  on-secondary-container: '#6d5200'
  tertiary: '#645e48'
  on-tertiary: '#ffffff'
  tertiary-container: '#a19a80'
  on-tertiary-container: '#36321f'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdea9'
  primary-fixed-dim: '#fcbb3b'
  on-primary-fixed: '#271900'
  on-primary-fixed-variant: '#5f4100'
  secondary-fixed: '#ffdf98'
  secondary-fixed-dim: '#f6be0a'
  on-secondary-fixed: '#251a00'
  on-secondary-fixed-variant: '#5a4300'
  tertiary-fixed: '#ebe2c6'
  tertiary-fixed-dim: '#cec6ab'
  on-tertiary-fixed: '#1f1c0a'
  on-tertiary-fixed-variant: '#4c4732'
  background: '#fcf9f8'
  on-background: '#1b1b1c'
  surface-variant: '#e5e2e1'
typography:
  grid-data:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
  grid-header:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
  ui-label:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  ui-small:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 14px
  panel-title:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
  toolbar-button:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 16px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  row-height: 30px
  cell-padding-x: 8px
  toolbar-height: 40px
  sidebar-width: 260px
  gutter: 1px
  container-gap: 12px
---

## Brand & Style
The design system is engineered for extreme information density and professional productivity, balancing the utilitarian efficiency of a spreadsheet with a sophisticated, warm aesthetic. It prioritizes clarity, rapid data scanning, and high-frequency interaction.

The style is **Corporate / Modern** with a focus on **Minimalism**. It utilizes a "Sheet-First" philosophy where the grid is the primary interface element rather than a secondary layout container. The emotional response is one of organized warmth—moving away from cold, sterile enterprise blues toward a focused, golden-hour clarity that reduces eye strain during long working sessions.

## Colors
The palette is centered on a "Golden Grid" concept. 
- **Primary Accent (#C98F00):** Used for critical UI chrome, primary actions, and the active cell/row indicators.
- **Surface Strategy:** The app uses a layered approach where the main canvas is pure white (#FFFFFF), and surrounding panels (sidebars, property inspectors) use the warm off-white (#F7F7F2) to create a subtle structural distinction.
- **Selection Logic:** Use the pale yellow wash (#FFF6D9) for multi-cell selections and row hovering to maintain warmth without vibrating against the text.
- **Semantic Feedback:** Status colors use a high-contrast ratio for accessibility while maintaining a soft background tint to integrate with the warm palette.

## Typography
This design system employs **Inter** for its exceptional legibility at small sizes and robust OpenType features. 

- **Tabular Numerals:** It is mandatory to enable `tnum` (tabular numerals) and `lnum` (lining numerals) for all data grid cells to ensure decimals and digits align perfectly across rows for easy comparison.
- **Vertical Density:** Line heights are kept tight (1.2x to 1.4x) to support the compressed row requirements.
- **Visual Hierarchy:** Use weight (Semi-Bold) and color (#6B6B63) rather than size to distinguish headers from data. This keeps the scale consistent across the entire application.

## Layout & Spacing
The layout follows a **Fixed Grid** philosophy typical of desktop productivity software. 

- **The Grid Canvas:** The central workspace is a rigid 1px-bordered grid. Row heights should default to 30px, allowing for exactly 13px text with 8px of vertical breathing room.
- **Density Tiers:**
    - **Compact:** 28px row height.
    - **Standard:** 30px row height.
    - **Comfortable:** 36px row height.
- **Panels:** Use a "pinned" layout model. Sidebars and toolbars have fixed dimensions to maximize the predictability of the data grid.
- **Margins:** External app margins are minimal (4px-8px) to maximize screen real estate. Internal cell padding is strictly 8px horizontal to prevent text from touching vertical gridlines.

## Elevation & Depth
This design system uses **Low-contrast outlines** and **Tonal layers** instead of traditional shadows to maintain a "flat" spreadsheet feel.

- **Grid Lines:** Defined by 1px solid borders (#DCDCD4).
- **Active State:** The active cell or focused element is elevated via a 2px solid border in the Primary Accent (#C98F00) rather than a shadow.
- **Modals & Popovers:** For floating elements like filter menus, use a single-pixel border (#DCDCD4) with a very soft, high-diffusion shadow (8px blur, 5% opacity) to provide just enough separation from the grid below.
- **Z-Axis Hierarchy:** The Toolbar sits highest, followed by Side Panels, with the Data Grid forming the base layer.

## Shapes
The shape language is strictly functional.
- **General Elements:** Buttons, input fields, and panels use a consistent 4px (Soft) radius.
- **Grid Components:** Cells remain perfectly square (0px radius) to ensure gridlines connect seamlessly.
- **Selection Highlights:** Use a 2px radius on selection background washes to soften the internal "glow" of selected data without breaking the grid alignment.

## Components
- **Buttons:** Primary buttons use a solid #C98F00 background with white text. Secondary buttons use a white background with a #DCDCD4 border and #1F1F1F text.
- **Data Cells:** Must support multiple states: Default, Hover (Selection Fill), Selected (Primary Border), and Edited (White background with focus ring).
- **Input Fields:** Flat styling with a 1px border. On focus, the border changes to Primary Gold with a 1px "halo" of the Bright Accent (#FFC61A).
- **Chips/Badges:** Small, 20px high, using the status color tokens. Text is always centered and semi-bold.
- **Toolbars:** Use the Panel Background (#F7F7F2) with a 1px bottom border. Icons should be 16px in size, set in #6B6B63, shifting to #C98F00 on hover.
- **Scrollbars:** Custom-styled to be thin (8px) with a #DCDCD4 track and a #C98F00 thumb on interaction to ensure they don't visually clutter the grid.