#!/usr/bin/env python3
"""xr_testpattern.py [SECONDS] [SIZE_M] [DIST_M] -- world-fixed test pattern for checking lens calibration.

A quad composition layer (the compositor does all distortion work, so this tests exactly the runtime's lens
correction) showing a grid, crosshair, circles, edge labels, fine text and colour bars, SIZE_M wide, DIST_M ahead
of where your head is when it starts. Straight lines must stay straight and the image must fuse in both eyes.
AERO_BG=0..255 sets the background grey (default 12): LCD dark-transition smear shows on dark backgrounds.
AERO_SWEEP=HZ: head-locked pattern sliding left/right at HZ -- keep the head still; a horizontal break in the
vertical lines is a tear (frame flipped mid-scanout), which head tracking cannot cause. AERO_Y=M raises it M metres.
AERO_PATTERN=bars: just five thick white vertical bars."""
import sys, time, ctypes, os, math
import glfw, xr, numpy as np
from OpenGL import GL
from PIL import Image, ImageDraw, ImageFont
from xr.utils.gl import ContextObject
from xr.utils.gl.glfw_util import GLFWOffscreenContextProvider

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 120
size_m = float(sys.argv[2]) if len(sys.argv) > 2 else 1.2
dist_m = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
S = 2048
BG = int(os.environ.get("AERO_BG", "12"))
SWEEP = float(os.environ.get("AERO_SWEEP", "0"))
Y = float(os.environ.get("AERO_Y", "0"))           # raise (m) the pattern, e.g. to reach the top of the lens

def pattern():
    if os.environ.get("AERO_PATTERN") == "bars":   # plain: 5 thick white vertical bars (tear test)
        im = Image.new("RGB", (S, S), (BG, BG, BG)); d = ImageDraw.Draw(im)
        for k in range(5): d.rectangle([k * S // 5 + S // 20, 0, k * S // 5 + S // 20 + S // 25, S], fill=(255, 255, 255))
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)
    im = Image.new("RGB", (S, S), (BG, BG, BG)); d = ImageDraw.Draw(im)
    for i in range(0, S + 1, 64):
        w = 6 if i % 256 == 0 else 2; c = (230, 230, 230) if i % 256 == 0 else (120, 120, 120)
        d.line([(i, 0), (i, S)], fill=c, width=w); d.line([(0, i), (S, i)], fill=c, width=w)
    c = S // 2
    for r in range(128, S, 256): d.ellipse([c - r, c - r, c + r, c + r], outline=(80, 200, 255), width=4)
    d.line([(c - 120, c), (c + 120, c)], fill=(255, 60, 60), width=8); d.line([(c, c - 120), (c, c + 120)], fill=(255, 60, 60), width=8)
    try: f = ImageFont.truetype("DejaVuSans-Bold.ttf", 90); fs = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError: f = fs = ImageFont.load_default()
    for txt, xy in (("TOP", (c, 70)), ("BOTTOM", (c, S - 70)), ("LEFT", (150, c)), ("RIGHT", (S - 170, c))):
        d.text(xy, txt, fill=(255, 220, 0), font=f, anchor="mm")
    for k in range(6):
        d.text((300, 300 + 30 * k), "The quick brown fox jumps over the lazy dog 0123456789", fill=(255, 255, 255), font=fs)
    for k, col in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]):
        d.rectangle([1300 + 140 * k, 1500, 1400 + 140 * k, 1800], fill=col)
    return np.asarray(im.transpose(Image.FLIP_TOP_BOTTOM).convert("RGBA"), dtype=np.uint8)

glfw.init_hint(glfw.PLATFORM, glfw.PLATFORM_X11)          # Monado's GL path needs GLX (XWayland)
# pyopenxr tries an EGL binding first, which Monado rejects for a GLX context: force the GLX (Xlib) binding.
import xr.utils.gl as _xgl, xr.utils.gl.context_object as _xco
def _glx_binding(cp):
    # pyopenxr leaves glxFBConfig/visualid empty; Monado rejects that, so look both up from the current context.
    from OpenGL import GLX
    b = _xgl.GLXGraphicsBinding(cp)
    gb = b.graphics_binding
    dpy, ctx = GLX.glXGetCurrentDisplay(), GLX.glXGetCurrentContext()
    fbid = ctypes.c_int(0)
    GLX.glXQueryContext(dpy, ctx, GLX.GLX_FBCONFIG_ID, ctypes.byref(fbid))
    n = ctypes.c_int(0)
    attrs = (ctypes.c_int * 3)(GLX.GLX_FBCONFIG_ID, fbid.value, 0)
    cfgs = GLX.glXChooseFBConfig(dpy, 0, attrs, ctypes.byref(n))
    if n.value < 1:
        raise RuntimeError("no GLX FBConfig for the current context")
    gb.glx_fbconfig = cfgs[0]
    vis = GLX.glXGetVisualFromFBConfig(dpy, cfgs[0])
    if vis:
        gb.visualid = vis.contents.visualid
    return b
_xgl.create_graphics_binding = _glx_binding
if hasattr(_xco, "create_graphics_binding"): _xco.create_graphics_binding = _xgl.create_graphics_binding
with ContextObject(
    context_provider=GLFWOffscreenContextProvider(gl_version=(4, 6)),
    instance_create_info=xr.InstanceCreateInfo(enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME]),
    reference_space_create_info=xr.ReferenceSpaceCreateInfo(reference_space_type=xr.ReferenceSpaceType.LOCAL),
) as ctx:
    fmts = xr.enumerate_swapchain_formats(ctx.session)
    fmt = next((f for f in fmts if f == GL.GL_SRGB8_ALPHA8), fmts[0])
    sc = xr.create_swapchain(ctx.session, xr.SwapchainCreateInfo(
        usage_flags=xr.SwapchainUsageFlags.SAMPLED_BIT | xr.SwapchainUsageFlags.COLOR_ATTACHMENT_BIT,
        format=fmt, sample_count=1, width=S, height=S, face_count=1, array_size=1, mip_count=1))
    imgs = xr.enumerate_swapchain_images(sc, xr.SwapchainImageOpenGLKHR)
    pix = pattern()
    for _ in range(len(imgs)):                                # upload the pattern into every swapchain image
        ix = xr.acquire_swapchain_image(sc, xr.SwapchainImageAcquireInfo())
        xr.wait_swapchain_image(sc, xr.SwapchainImageWaitInfo(timeout=xr.INFINITE_DURATION))
        GL.glBindTexture(GL.GL_TEXTURE_2D, imgs[ix].image)
        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, S, S, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, pix)
        xr.release_swapchain_image(sc, xr.SwapchainImageReleaseInfo())
    space = ctx.space
    if SWEEP:   # head-locked: VIEW space follows the head, so only the pattern moves on the panel
        space = xr.create_reference_space(ctx.session, xr.ReferenceSpaceCreateInfo(
            reference_space_type=xr.ReferenceSpaceType.VIEW, pose_in_reference_space=xr.Posef()))
    quad = xr.CompositionLayerQuad(space=space, eye_visibility=xr.EyeVisibility.BOTH)
    quad.sub_image.swapchain = sc
    quad.sub_image.image_rect.extent[:] = [S, S]
    quad.pose = xr.Posef(orientation=xr.Quaternionf(0, 0, 0, 1), position=xr.Vector3f(0, Y, -dist_m))
    quad.size = xr.Extent2Df(size_m, size_m)
    t0 = time.time(); print(f"showing {size_m} m pattern at {dist_m} m for {secs:.0f} s", flush=True)
    for frame_state in ctx.frame_loop():
        for _ in ctx.view_loop(frame_state):                  # projection layer: plain dark grey surroundings
            GL.glClearColor(BG / 255, BG / 255, BG / 255, 1); GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        ix = xr.acquire_swapchain_image(sc, xr.SwapchainImageAcquireInfo())
        xr.wait_swapchain_image(sc, xr.SwapchainImageWaitInfo(timeout=xr.INFINITE_DURATION))
        xr.release_swapchain_image(sc, xr.SwapchainImageReleaseInfo())
        ctx.render_layers.append(ctypes.byref(quad))
        if SWEEP: quad.pose.position.x = 0.4 * dist_m * math.sin(2 * math.pi * SWEEP * (time.time() - t0))
        if time.time() - t0 > secs: break
