"""识别与窗口输入边界：图像为本地生成的替身，不截取用户客户端。"""
from lol_words.system import runtime_bootstrap
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from lol_words.lobby.client_surface import ClientSurface,Frame,Evidence,match_avatar


def picture(duplicate=False,flat=False):
    pixels=np.zeros((500,800,4),dtype=np.uint8);pixels[:,:,:3]=30;pixels[:,:,3]=255
    patch=np.random.default_rng(123).integers(0,255,(36,36,3),dtype=np.uint8)
    if flat:patch[:]=[30,80,150]
    pixels[30:66,700:736,:3]=patch
    if duplicate:pixels[30:66,745:781,:3]=patch
    return Frame(800,500,pixels.tobytes())


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.area.name)
        self.native=Mock();self.native.foreground_handle.return_value=7
        self.surface=ClientSurface(self.native,self.root)
        self.reference=dict(width=800,height=500,rect=[700/800,30/500,736/800,66/500])
    def tearDown(self):self.area.cleanup()

    def test_calibrated_avatar_is_found_near_verified_position(self):
        frame=picture();template=frame.image().copy(700,30,36,36)
        match=match_avatar(frame,template,self.reference)
        self.assertEqual(match['rect'],[700,30,736,66])

    def test_two_independent_matching_avatars_are_ambiguous(self):
        frame=picture(duplicate=True);template=frame.image().copy(700,30,36,36)
        self.assertIsNone(match_avatar(frame,template,self.reference))

    def test_flat_or_changed_avatar_is_not_treated_as_safe_match(self):
        frame=picture(flat=True);template=frame.image().copy(700,30,36,36)
        self.assertIsNone(match_avatar(frame,template,self.reference))
        template=picture().image().copy(700,30,36,36)
        self.assertIsNone(match_avatar(frame,template,self.reference))

    def test_size_and_pixel_scaling_are_supported(self):
        original=picture();image=original.image().scaled(1000,625,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
        frame=Frame(image.width(),image.height(),bytes(image.constBits()))
        result=match_avatar(frame,original.image().copy(700,30,36,36),self.reference)
        self.assertIsNotNone(result)
        self.assertLess(abs(result['rect'][0]-875),2)

    def test_avatar_calibration_only_saves_small_crop_and_relative_bounds(self):
        self.surface.save_avatar(picture(),[700,30,736,66])
        saved=QImage(str(self.root/'runtime/avatar-template.png'))
        self.assertEqual((saved.width(),saved.height()),(36,36))
        data=json.loads((self.root/'runtime/avatar-template.json').read_text(encoding='utf-8'))
        self.assertEqual(data['width'],800);self.assertEqual(data['rect'],self.reference['rect'])
        self.assertEqual(len(list((self.root/'runtime').iterdir())),2)

    def test_calibration_outside_upper_right_or_without_pattern_is_rejected(self):
        for frame,rect in [(picture(),[100,100,140,140]),(picture(),[700,300,736,336]),(picture(flat=True),[700,30,736,66])]:
            with self.subTest(rect=rect),self.assertRaises(ValueError):self.surface.save_avatar(frame,rect)

    def test_changed_foreground_or_cancelled_action_never_invokes(self):
        evidence=Evidence(dict(hwnd=7),avatar=dict(source='uia'))
        with patch.object(self.surface,'accessible') as invoke:
            self.assertFalse(self.surface.act('avatar',evidence,lambda:False))
            self.native.foreground_handle.return_value=9
            with patch.object(self.surface,'valid',return_value=True):
                self.assertFalse(self.surface.act('avatar',evidence,lambda:True))
            invoke.assert_not_called()

    def test_stale_ocr_pixels_prevent_click_even_in_same_window(self):
        old=picture();pixels=np.frombuffer(old.pixels,np.uint8).copy().reshape(500,800,4)
        pixels[400:430,200:400,:3]=250
        fresh=Frame(800,500,pixels.tobytes())
        evidence=Evidence(dict(hwnd=7),skip=dict(source='ocr',rect=[200,400,400,430]),frame=old)
        with patch.object(self.surface,'valid',return_value=True),patch('lol_words.lobby.client_surface.capture_client',return_value=fresh):
            self.assertFalse(self.surface.act('skip',evidence,lambda:True))
        self.native.u.SendInput.assert_not_called()

    def test_multiple_client_windows_are_not_randomly_selected(self):
        self.native.window_inventory.return_value=[dict(path='C:/LOL/LeagueClientUx.exe',hwnd=7),dict(path='C:/LOL/LeagueClientUx.exe',hwnd=8)]
        self.assertIsNone(self.surface.window())
        self.native.window_inventory.return_value=[dict(path='C:/LOL/League of Legends.exe',hwnd=7)]
        self.assertIsNone(self.surface.window())

    def test_ocr_timeout_is_bounded_and_temporary_image_is_removed(self):
        import subprocess
        with patch('lol_words.lobby.client_surface.subprocess.run',side_effect=subprocess.TimeoutExpired('ocr',4)):
            self.assertIsNone(self.surface.ocr(picture()))
        self.assertEqual(list((self.root/'runtime/client-ocr').glob('*.png')),[])
