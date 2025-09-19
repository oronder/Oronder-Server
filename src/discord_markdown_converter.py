import re

from markdownify import MarkdownConverter


class DiscordMarkdownConverter(MarkdownConverter):
    def __init__(self, **options):
        super().__init__(**options)

    # def convert_strong(self, el, text, parent_tags):
    #     return f"**{text}**"
    #
    # def convert_em(self, el, text, parent_tags):
    #     return f"*{text}*"
    #
    # def convert_code(self, el, text, parent_tags):
    #     return f"`{text}`"
    #
    # def convert_blockquote(self, el, text, parent_tags):
    #     return "\n".join(f"> {line}" for line in text.split("\n"))
    # def convert_table(self, el, text, **kwargs):
    #     return ''  # Return an empty string instead of converting the table

    def convert_td(self, el, text, parent_tags):
        return f"{text}. " if text.isnumeric() else text

    def convert_th(self, el, text, parent_tags):
        return text

    def convert_tr(self, el, text, parent_tags):
        return f"{text}\n"

    def convert(self, html):
        markdown = super().convert(html)

        # Post-process to handle any remaining Discord-specific markdown
        markdown = re.sub(r"<u>(.*?)</u>", r"__\1__", markdown)  # Underline
        markdown = re.sub(r"<del>(.*?)</del>", r"~~\1~~", markdown)  # Strikethrough
        markdown = re.sub(r"### (.*)", r"**\1**", markdown)

        markdown = re.sub(r"@item\\\[([^\\]+)\\\|[^\]]+\]", r"\1", markdown)
        markdown = re.sub(
            r"\\\[\\\[/[A-Za-z]+ ((?:\d*d\d+(?:\s*\+\s*(?:\d+|\d*d\d+))*)|d\d+)\]\]",
            r"**\1**",
            markdown,
        )
        markdown = re.sub(r"\\&Reference\\\[[^\\]+\\=([^\]]+)\]", r"\1", markdown)
        return markdown


# Create shorthand method for conversion
def md(html, **options):
    return DiscordMarkdownConverter(**options).convert(html)
