# Changelog

Each version's notes are shown in the app's "Update available" window.
When you raise VERSION in app_version.py, add a section for it at the top.

## 2.12.0
- Halloween themes are gone - replaced by 5 Christmas and 5 Thanksgiving themes
- Christmas: Christmas Eve (tree, presents and Santa's sleigh), Candy Cane, Frosty (snowman), North Pole (northern lights), Gingerbread (gingerbread house)
- Thanksgiving: Harvest (hay and pumpkins), Turkey Trot (a turkey that walks the fence - click it), Cranberry (red barn), Maple (geese flying over), Pumpkin Spice
- Each holiday theme has its own moving sidebar scene - click the snow, peppermints and leaves
- Settings groups the themes into Christmas, Thanksgiving and Everyday
- If you were on a Halloween theme you're moved to Harvest automatically

## 2.11.0
- Stop button on every upload - stops a running upload mid-transfer (also while zipping or hashing), or removes it from the queue before it starts
- Stopped uploads show as "Stopped", aren't added to History, and are cleared with "Clear finished"
- A watched mod whose upload you stop stays marked as changed so you can upload it later
- PSD Builder: reads Painter layers with its own reader and keeps rough, spec and Norm in separate folders

## 2.10.0
- PSD Builder no longer needs Photoshop - the app writes the PSDs itself in a few seconds
- One PSD per texture set: pick the Painter export folder, check the names (taken from the .tga files, e.g. texture_plate.1001.psd becomes plate.psd), press Build
- Each PSD has the locked Hide me layer, rough (_r), spec (_s), Norm (Bikes green-flipped normal + _n) and Base folders with the art and Shading
- Shading: ao Multiply, 2D Overlay 60%, curv Overlay 25%, _mask Multiply - pick Chain or Pendant shading per set, and change them in Shading settings
- Colour fills are left for you to add
- "Open in Photoshop when done" opens the new PSDs for you
- Photoshop is still used for "Export game .tga from a PSD", so your masks and colours come out exactly right

## 2.9.0
- New PSD Builder page in the sidebar: turns a Substance Painter export into your Chain + Pendant PSD in Photoshop
  - Finds the texture sets by itself, whatever Painter named them - you just pick which is Chain and which is Pendant
  - Base group gets the art, two colour fills (Multiply) for your masks, and Shading: ao 70% Multiply, 2D 60% Soft Light, curv Overlay (20% chain, 39% pendant)
  - Norm gets the normal plus "Inverted for Bikes" (green flipped); Spec and Rough get their maps
  - Choose PSD only, or PSD + game .tga files (X.tga, X_n, X_s, X_r in a Game textures folder)
  - "Export game .tga from a PSD" makes the game files from your finished PSD after you've done the masks and colours

## 2.8.0
- Settings is now a page inside the app (sidebar > Settings, or the gear) instead of a pop-up window
- Smaller theme tiles - click one to switch instantly
- Storage details and the version / update check live on the same page

## 2.7.0
- Themes switch instantly - click one in Settings and the whole app changes right away, no restart
- Uploads keep running, and your folder, notes, storage and sign-in stay put while you switch

## 2.6.0
- Four new Halloween themes, each with its own animated sidebar scene:
  - Blood Moon: a huge red moon over the graveyard, with bats
  - Ghostly: little ghosts drift and flicker through rolling fog (click one to make it vanish)
  - Witch's Brew: a bubbling cauldron under a green moon
  - Candy Corn: candy corn falls from the sky (click to eat one)
- Settings shows all 12 themes, six per row

## 2.5.0
- New Terps WorkFlow logo (black-and-white TW monogram) in the title bar, taskbar, on the exe, in the sidebar and on the sign-in screen

## 2.4.1
- Fixed the "Failed to remove temporary directory" warning after updating or switching themes
- Wider sidebar so the app name, version and settings button fit

## 2.4.0
- New name: Terps WorkFlow
- See how much of your MediaFire storage you're using, at the bottom of the sidebar and in Settings
- New Settings window (gear button next to the app name) with a theme gallery
- Six new themes: Terps, Ocean, Sunset, Racing, Neon and Arctic (light)

## 2.3.3
- Fixed the last step on the Updates page showing as an empty black bar on Windows

## 2.3.2
- Fixed "Failed to load Python DLL" when the app restarts itself after an update or a theme switch

## 2.3.1
- Fixed the "How a new version gets here" steps showing as an empty black box on Windows
- Clearer message when the app can't see releases on GitHub

## 2.3.0
- Halloween theme: pumpkin orange and midnight purple, a flickering jack-o'-lantern logo, and a night-sky scene in the sidebar
- Bats in the sidebar scene fly away from your cursor, and clicking one makes it vanish in a puff; a spider drops down on its thread to follow you
- New theme switch at the bottom of the sidebar (Midnight or Halloween)

## 2.2.1
- More reliable update downloads from GitHub

## 2.2.0
- The app now updates itself from GitHub: it checks for a new version when it opens, shows what's new and installs it for you
- New Updates page in the sidebar
- Dark, modern look with a sidebar, drag-and-drop highlight, upload cards and pop-up notices

## 2.1.0
- First version with the dark interface
