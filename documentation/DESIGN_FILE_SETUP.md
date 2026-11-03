# SialviPrint — Adobe / AutoCAD File Support

SialviPrint now accepts these design formats in the User file picker:

- Adobe Photoshop: `.psd`
- Adobe Illustrator: `.ai`
- AutoCAD: `.dwg`, `.dxf`

The important part is that these are not sent to Windows using the normal
file-association `print` verb. The Admin PC first turns the source into the
PDF that SialviPrint will actually print, then the existing PDF page-counting
and silent-print pipeline handles it. This keeps pricing, page ranges,
copies and customer print properties on the same path as ordinary PDFs.

## Admin PC runtime requirements

### Photoshop / Illustrator

Install **ImageMagick** on the Admin PC and make sure `magick.exe` is either
on PATH or installed in the normal `C:\Program Files\ImageMagick-*` location.

- PSD is read directly by ImageMagick.
- AI is read through ImageMagick's PostScript/Ghostscript delegate, so the
  ImageMagick installation must include that delegate.

SialviPrint also checks the common Windows install locations automatically;
PATH is not mandatory.

### AutoCAD

- `.dxf` is rendered directly by the bundled Python CAD renderer (`ezdxf`).
- `.dwg` first needs **ODA File Converter** on the Admin PC, because the
  Python renderer reads DXF rather than DWG. Set the `ODA_FILE_CONVERTER_PATH`
  environment variable if it is not installed in the normal ODA folder.

ODA's official page documents its Windows command-line converter and its
source/target directory, output version/type, recursive and audit arguments.
Check the licensing terms for the exact ODA distribution you use before
shipping it in a commercial environment.

## What happens if a dependency is missing?

The upload is accepted only if the file extension is supported. During page
counting, SialviPrint then gives a clear dependency error instead of guessing
page count. A file is never silently marked printable when the conversion
engine is unavailable.

## Windows test order

1. Install ImageMagick on the Admin PC.
2. Upload one `.psd`; confirm a page count is returned.
3. Upload one `.ai`; confirm a page count is returned.
4. Install/configure ODA File Converter if DWG is required.
5. Upload one `.dxf`; confirm a page count and print it.
6. Upload one `.dwg`; confirm conversion, page count and print it.
7. Test copies, page range and the Glossy/Matte/Luster print-properties path.

The sandbox can verify the conversion code with real ImageMagick, ezdxf and
PDF parsing, but it cannot prove a Windows printer physically produced paper.
