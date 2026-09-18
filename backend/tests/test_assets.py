import base64
import logging
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from backend.app.assets import processor
from backend.app.assets.background import NoOpBackgroundRemover
from backend.app.config import AssetSettings, asset_settings


class FakeRemover:
    def __init__(self):
        self.calls = 0

    def remove(self, image):
        self.calls += 1
        return image


class FailingRemover:
    def remove(self, image):
        image.paste('black', (0, 0, *image.size))
        raise RuntimeError('failure')


def decoded(asset):
    return Image.open(BytesIO(base64.b64decode(asset.data_uri.split(',', 1)[1])))


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / 'photo.png'
    Image.new('RGB', (100, 200), 'red').save(path)
    return path


def test_exif(tmp_path):
    original = Image.new('RGB', (40, 80), 'red')
    exif = original.getexif()
    exif[274] = 6
    original.save(tmp_path / 'rotated.jpg', exif=exif)
    asset = processor.prepare_asset('rotated.jpg', tmp_path, AssetSettings(cache_dir=tmp_path/'cache', padding_ratio=0))
    with decoded(asset) as result:
        assert result.mode == 'RGBA'
        assert result.size == (80,40)
        assert not result.getexif().get(274)


def test_rgba_and_original_untouched(photo):
    original = photo.read_bytes()
    asset = processor.prepare_asset(photo.name, photo.parent)
    with decoded(asset) as result:
        assert result.mode == 'RGBA'
    assert photo.read_bytes() == original


def test_transparent_crop_padding():
    source = Image.new('RGBA', (200, 200))
    source.paste('red', (70, 50, 130, 150))
    result = processor.crop_to_content(source, .1)
    assert result.size == (72,120)
    assert result.getchannel('A').getbbox() == (6,10,66,110)


@pytest.mark.parametrize('mode', ['RGB','RGBA'])
def test_crop_at_edges_and_without_alpha(mode):
    source = Image.new(mode,(20,40),'red')
    result = processor.crop_to_content(source,0)
    assert result.size == source.size
    assert result.mode == 'RGBA'
    assert result.getpixel((0,0)) == (255,0,0,255)


def test_empty_transparent(tmp_path, caplog):
    Image.new('RGBA',(40,40)).save(tmp_path/'empty.png')
    assert processor.prepare_asset('empty.png',tmp_path) is None
    assert 'completamente transparente' in caplog.text


def test_tiny_no_upscale():
    result = processor.normalize(Image.new('RGBA',(1,2),'red'),0)
    assert result.size == (1,2)


def test_proportion_and_size():
    result = processor.normalize(Image.new('RGBA',(1200,600),'red'),.05)
    assert result.size == (600,300)


@pytest.mark.parametrize('ratio', [-1, .26, float('nan'), float('inf')])
def test_invalid_padding(ratio):
    with pytest.raises(ValueError):
        processor.crop_to_content(Image.new('RGB',(2,2)),ratio)


def test_noop():
    image = Image.new('RGB',(2,2))
    assert NoOpBackgroundRemover().remove(image) is image


def test_cache_hit_miss(photo, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    settings = AssetSettings(cache_dir=tmp_path/'cache',remove_background=True)
    remover = FakeRemover()
    first = processor.prepare_asset(photo.name,tmp_path,settings,remover)
    second = processor.prepare_asset(photo.name,tmp_path,settings,remover)
    assert first == second
    assert remover.calls == 1
    assert len(list(settings.cache_dir.glob('*.png'))) == 1
    assert 'asset_cache_miss' in caplog.text and 'asset_cache_hit' in caplog.text
    assert 'asset_normalized' in caplog.text and 'background_removal_started' in caplog.text
    assert 'base64' not in caplog.text


@pytest.mark.parametrize('change', ['bytes','padding','enabled','version','model'])
def test_cache_invalidation(photo,tmp_path,monkeypatch,change):
    settings = AssetSettings(cache_dir=tmp_path/'cache')
    fake = FakeRemover()
    if change == 'model':
        settings = settings.model_copy(update={'remove_background':True})
        monkeypatch.setattr(processor,'model_bytes',lambda: b'model1')
        monkeypatch.setattr(processor,'RembgBackgroundRemover',lambda:fake)
    processor.prepare_asset(photo.name,tmp_path,settings)
    if change == 'bytes':
        Image.new('RGB',(100,200),'blue').save(photo)
    elif change == 'padding':
        settings = settings.model_copy(update={'padding_ratio':.1})
    elif change == 'enabled':
        settings = settings.model_copy(update={'remove_background':True})
    elif change == 'version':
        monkeypatch.setattr(processor,'PIPELINE_VERSION','test-next')
    else:
        monkeypatch.setattr(processor,'model_bytes',lambda: b'model2')
    processor.prepare_asset(photo.name,tmp_path,settings, fake if change == 'enabled' else None)
    assert len(list(settings.cache_dir.glob('*.png'))) == 2


def test_removal_failure_fallback_and_retry(photo,tmp_path,caplog):
    settings = AssetSettings(cache_dir=tmp_path/'cache',remove_background=True,padding_ratio=0)
    result = processor.prepare_asset(photo.name,tmp_path,settings,FailingRemover())
    with decoded(result) as image:
        assert image.getpixel((50,100)) == (255,0,0,255)
        assert image.size == (100,200)
    assert 'background_removal_failed' in caplog.text
    assert not list(settings.cache_dir.glob('*.png'))
    assert processor.prepare_asset(photo.name,tmp_path,settings,FakeRemover())
    assert len(list(settings.cache_dir.glob('*.png'))) == 1


def test_empty_removal_fallback(photo,tmp_path):
    class EmptyRemover:
        def remove(self,image):
            return Image.new('RGBA',image.size)
    result = processor.prepare_asset(photo.name,tmp_path,AssetSettings(remove_background=True,cache_dir=tmp_path/'cache'),EmptyRemover())
    assert decoded(result).getchannel('A').getbbox() is not None


def test_missing_and_invalid(tmp_path):
    assert processor.prepare_asset('missing.png',tmp_path) is None
    (tmp_path/'invalid.png').write_text('not an image')
    assert processor.prepare_asset('invalid.png',tmp_path) is None


def test_size_limit(photo,tmp_path,monkeypatch):
    monkeypatch.setattr(processor,'MAX_BYTES',1)
    assert processor.prepare_asset(photo.name,tmp_path) is None


def test_pixel_limit(photo,tmp_path,monkeypatch):
    monkeypatch.setattr(processor,'MAX_PIXELS',100)
    assert processor.prepare_asset(photo.name,tmp_path) is None


def test_urls_and_symlink(tmp_path,photo):
    root=tmp_path/'assets'
    root.mkdir()
    (root/'link.png').symlink_to(photo)
    for path in ['link.png','../photo.png','https://example.com/photo.png','file:///etc/passwd']:
        assert processor.prepare_asset(path,root) is None


def test_corrupt_cache_recovers(photo,tmp_path):
    settings=AssetSettings(cache_dir=tmp_path/'cache')
    first=processor.prepare_asset(photo.name,tmp_path,settings)
    cache=next(settings.cache_dir.glob('*.png'))
    cache.write_bytes(b'corrupt')
    assert processor.prepare_asset(photo.name,tmp_path,settings)==first


def test_unwritable_cache_fallback(photo,tmp_path,caplog):
    blocked=tmp_path/'blocked'
    blocked.write_text('file')
    assert processor.prepare_asset(photo.name,tmp_path,AssetSettings(cache_dir=blocked))
    assert 'asset_cache_write_failed' in caplog.text


@pytest.mark.parametrize('variable,value', [('BANNER_REMOVE_BACKGROUND','maybe'),('BANNER_ASSET_PADDING_RATIO','NaN'),('BANNER_ASSET_PADDING_RATIO','-1'),('BANNER_ASSET_CACHE_DIR','')])
def test_settings_validation(monkeypatch,variable,value):
    monkeypatch.setenv(variable,value)
    with pytest.raises(ValidationError):
        asset_settings()


@pytest.mark.parametrize('size,shape', [((100,400),'vertical'),((400,100),'horizontal'),((200,200),'square')])
def test_aspect_ratio(size,shape):
    assert processor.PreparedAsset('',*size).shape == shape
