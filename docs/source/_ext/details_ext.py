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
    <div>
        (rest of content goes here)
    </div>
</details>

That is, you can style the part that expanded with CSS like:

.. code-block:: css

    details div {
    }

And the label (when collapsed) with:

.. code-block:: css

    details summary {
    }
"""


from docutils import nodes
from docutils.parsers import rst


class details(nodes.Element, nodes.General):
    pass


class summary(nodes.TextElement, nodes.General):
    pass


class DetailsDirective(rst.Directive):
    has_content = True
    required_arguments = 1
    final_argument_whitespace = True
    option_spec = {
        "class": rst.directives.class_option,
        "name": rst.directives.unchanged,
    }

    def run(self):
        details_node = details()

        # Pass along the first argument of the directive to the node
        details_node["heading"] = self.arguments[0]
        self.state.nested_parse(self.content, self.content_offset, details_node)
        return [details_node]


def visit_details(self, node):
    heading = node["heading"]
    self.body.append(
        f"<details><summary>{heading}</summary><div>"
    )


def depart_details(self, node):
    self.body.append("</div></details>")


def setup(app):
    """Set up sphinx extension"""
    app.add_directive("details", DetailsDirective)
    app.add_node(details, html=(visit_details, depart_details))
    return {"version": "0.0.1", "parallel_read_safe": True, "parallel_write_safe": True}
