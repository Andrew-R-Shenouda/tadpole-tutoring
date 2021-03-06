from jinja2 import Template, Markup
import glob

files = glob.glob("*.html")
pages = []
imports = []
for each_file in files:
    with open(each_file) as f:
        if "<!DOCTYPE html>" in next(f).rstrip():
            pages.append(each_file)
        else:
            imports.append(each_file)

args = {}
for each_import in imports:
    name = each_import.split(".")[0]
    with open(each_import) as f:
        import_html = f.read()
    args[name] = import_html

# with open("footer.html") as f:
    # footer = f.read()
# args["footer"] = footer

for each_file in pages:
    with open(each_file) as f:
        html = f.read().rstrip()
        template = Template(html)
        rendered = template.render(**args)

    with open("compiled/" + each_file, "w") as f:
        f.write(rendered)
