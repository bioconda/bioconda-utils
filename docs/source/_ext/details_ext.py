"""
Allows the use of a directive to create HTML <details> foldable sections.

.. details:: label goes here

    Arbitrary directive content

    - including other kinds of formatting

    .. code-block:: python

        # and more
        x = 'more'

The resulting HTML has the structure:

<details>
    <summary>
        label goes here
    </summary>
    <div class="details">
        (rest of content goes here)
    </div>
</details>

That is, you can style the part that expanded with CSS like::

    details div { }

And the label (when collapsed) with::

    details summary { }

You can also add anchors, which when visited (and when paired with the proper
javascript) will unfold the details such that the anchors can be used as
permalinks, like this:

.. details: Label here
    :anchor: label-here

    Arbitrary content

Then you can link back to it with `see here <#label-here>`_.
"""


from docutils import nodes
from docutils.parsers import rst


class details(nodes.Element, nodes.General):
    pass


class summary(nodes.TextElement, nodes.General):
    pass


class DetailsDirective(rst.Directive):
    has_content = True
    required_arguments = 0
    optional_arguments = 1
    final_argument_whitespace = True
    option_spec = {
        "class": rst.directives.class_option,
        "name": rst.directives.unchanged,
        "anchor": rst.directives.unchanged,
    }

    def run(self):
        details_node = details()

        # Pass along the first argument of the directive to the node
        details_node["heading"] = self.arguments[0]
        details_node["anchor"] = self.options.get("anchor", None)
        self.state.nested_parse(self.content, self.content_offset, details_node)
        return [details_node]


def visit_details(self, node):
    heading = node["heading"]
    anchor = node["anchor"]
    if anchor:
        self.body.append(
            f"<details><summary>{heading}</summary>"
            f'<div id={anchor} class="details">'
            f"<p><i>{heading}</i>"
            f'<a class="headerlink" title="Permalink" href="#{anchor}">¶</a></p>'
        )
    else:
        self.body.append(
            f"<details><summary>{heading}</summary>"
            f'<div class="details">'
            f"<p><i>{heading}</i></p>"
        )


def depart_details(self, node):
    self.body.append("</div></details>")


def setup(app):
    """Set up sphinx extension"""
    app.add_directive("details", DetailsDirective)
    app.add_node(details, html=(visit_details, depart_details))
    return {"version": "0.0.1", "parallel_read_safe": True, "parallel_write_safe": True}
