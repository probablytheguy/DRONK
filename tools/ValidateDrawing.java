import java.io.File;
import javax.xml.XMLConstants;
import javax.xml.validation.SchemaFactory;
import javax.xml.transform.stream.StreamSource;

/** Validate using the public Free Form schema and its locally downloaded includes.
 * Usage: java tools/ValidateDrawing.java DRAWING.xsd TRAIL.cot ...
 * No network schema resolution is allowed. Schemas are not redistributed.
 */
class ValidateDrawing {
    public static void main(String[] args) throws Exception {
        if (args.length < 2) throw new IllegalArgumentException("DRAWING.xsd TRAIL.cot ...");
        var factory = SchemaFactory.newInstance(XMLConstants.W3C_XML_SCHEMA_NS_URI);
        factory.setProperty(XMLConstants.ACCESS_EXTERNAL_DTD, "");
        factory.setProperty(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "file");
        var schema = factory.newSchema(new File(args[0]));
        for (int i = 1; i < args.length; i++) {
            var validator = schema.newValidator();
            validator.setProperty(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            validator.setProperty(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            validator.validate(new StreamSource(new File(args[i])));
            System.out.println("PASS: full Free Form drawing schema: " + args[i]);
        }
    }
}
